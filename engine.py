"""Laxmi Chit Fund - Season 5 : data engine.

Pulls the live FPL API, computes every prize category and the mini-tournament
draws, and writes data.json for the dashboard. Designed to run on GitHub
Actions after each gameweek. Safe to run pre-season: everything simply reports
an empty/"not started" state until GW1 is scored.

Usage:  python engine.py
"""
import hashlib
import json
import os
import sys
import time

import fplapi
import compute
import config as C

DRAWS_DIR = os.path.join(os.path.dirname(__file__), "draws")
OUT = os.path.join(os.path.dirname(__file__), "data.json")
CHIP_KEYS = {"3xc", "bboost", "wildcard", "freehit"}


# ---- gameweek state ---------------------------------------------------------

def gw_state(boot):
    events = boot["events"]
    finished = [e["id"] for e in events if e["finished"] and e["data_checked"]]
    current = next((e["id"] for e in events if e["is_current"]), None)
    nxt = next((e for e in events if e["is_next"]), None)
    last_finished = max(finished) if finished else 0
    return {
        "finished": finished,
        "last_finished": last_finished,
        "current": current,
        "next_id": nxt["id"] if nxt else None,
        "next_deadline": nxt["deadline_time"] if nxt else None,
        "started": last_finished > 0,
    }


# ---- build one manager record ----------------------------------------------

def build_manager(entry_id, team, name, finished_gws, boot_elements):
    rec = {"id": entry_id, "team": team, "name": name,
           "total": None, "rank": None, "last_rank": None, "event_total": None,
           "history": {}, "captain": {}}
    hist = fplapi.entry_history(entry_id)
    if hist:
        chips_by_gw = {c["event"]: c["name"] for c in hist.get("chips", [])}
        for h in hist.get("current", []):
            gw = h["event"]
            hit = h.get("event_transfers_cost", 0)
            gross = h.get("points", 0)
            rec["history"][gw] = {
                "gross": gross, "hit": hit, "net": gross - hit,
                "bench": h.get("points_on_bench", 0),
                "overall_rank": h.get("overall_rank"),
                "chip": chips_by_gw.get(gw),
            }
        rec["total"] = hist["current"][-1]["total_points"] if hist.get("current") else None
    # captain per finished GW (needs picks + that GW's live points)
    for gw in finished_gws:
        if gw not in rec["history"]:
            continue
        picks = fplapi.entry_picks(entry_id, gw)
        if not picks:
            continue
        cap = next((p for p in picks.get("picks", []) if p.get("is_captain")), None)
        if not cap:
            continue
        live = fplapi.event_live(gw)
        pts = 0
        if live:
            el = next((e for e in live.get("elements", [])
                       if e["id"] == cap["element"]), None)
            if el:
                pts = el["stats"]["total_points"]
        rec["captain"][gw] = {"pts": pts, "mult": max(cap.get("multiplier", 2), 2),
                              "element": cap["element"]}
    return rec


# ---- overall standings from the league endpoint -----------------------------

def apply_league_ranks(managers, league_id):
    data = fplapi.league_standings(league_id, 1)
    by_id = {m["id"]: m for m in managers}
    results = (data or {}).get("standings", {}).get("results", [])
    for r in results:
        m = by_id.get(r["entry"])
        if m:
            m["rank"] = r["rank"]
            m["last_rank"] = r["last_rank"]
            m["total"] = r["total"]
            m["event_total"] = r["event_total"]
    # pre-season fallback: no ranks yet -> order by total (all None -> keep input)
    if not results:
        for i, m in enumerate(sorted(managers,
                                     key=lambda x: -(x["total"] or 0)), 1):
            m["rank"] = i if m["total"] else None
    return managers


# ---- mini-tournament draws (create once, then immutable) --------------------

def draw_seed(ranked_pairs):
    """Deterministic seed from the qualifying standings, so the draw is
    reproducible/auditable by anyone holding the same standings."""
    blob = json.dumps(ranked_pairs, sort_keys=True).encode()
    return int(hashlib.sha256(blob).hexdigest()[:12], 16)


def load_or_create_draw(mt, managers, gws):
    path = os.path.join(DRAWS_DIR, f"mt{mt['id']}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    if mt["seed_after"] not in gws["finished"]:
        return None  # not time to draw yet
    ranked = sorted([m for m in managers if m["rank"]],
                    key=lambda m: m["rank"])[:C.MT_QUALIFY_COUNT]
    if len(ranked) < C.MT_QUALIFY_COUNT:
        return None
    ranked_pairs = [(m["id"], m["total"]) for m in ranked]
    seed = draw_seed(ranked_pairs)
    groups = compute.draw_groups([m["id"] for m in ranked], seed,
                                 C.MT_GROUP_COUNT, C.MT_GROUP_SIZE)
    draw = {"mt": mt["id"], "seed": seed,
            "qualified": [m["id"] for m in ranked], "groups": groups}
    os.makedirs(DRAWS_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(draw, f, indent=1)
    return draw


def build_mini_tournament(mt, managers, gws):
    by_id = {m["id"]: m for m in managers}
    draw = load_or_create_draw(mt, managers, gws)
    base = {"id": mt["id"],
            "window": f"GW{mt['group'][0]}–GW{mt['ko'][-1]}",
            "group_gws": mt["group"], "ko_gws": mt["ko"], "status": "upcoming",
            "groups": None, "ko": None}
    if not draw:
        return base
    scores = {i: {gw: compute.net_score(by_id[i], gw)
                  for gw in mt["group"] + mt["ko"] if by_id.get(i)}
              for i in draw["qualified"]}
    hits = {i: sum(by_id[i]["history"].get(gw, {}).get("hit", 0)
                   for gw in mt["group"]) for i in draw["qualified"] if by_id.get(i)}

    def name_of(i):
        m = by_id.get(i, {})
        return {"id": i, "team": m.get("team", "?"), "name": m.get("name", "?")}

    tables = []
    for gi, grp in enumerate(draw["groups"]):
        tab = compute.group_table(grp, mt["group"], scores, hits)
        for r in tab:
            r.update(name_of(r["id"]))
        tables.append({"group": "ABCDEF"[gi], "rows": tab})

    # extra info for knockout tiebreaks
    extra = {}
    for i in draw["qualified"]:
        m = by_id.get(i)
        if not m:
            continue
        extra[i] = {}
        for gw in mt["ko"]:
            h = m["history"].get(gw, {})
            cap = m["captain"].get(gw, {})
            extra[i][gw] = {"cap": cap.get("pts", 0) * cap.get("mult", 2),
                            "bench": h.get("bench", 0),
                            "bb": h.get("chip") == "bboost",
                            "rank": h.get("overall_rank") or 10**9}

    ko = None
    group_done = all(compute.net_score(by_id[i], mt["group"][-1]) is not None
                     for i in draw["qualified"] if by_id.get(i))
    if group_done:
        seeds = compute.pick_advancers([t["rows"] for t in tables])
        for s in seeds:
            s.update(name_of(s["id"]))
        bracket = compute.run_bracket(seeds, mt["ko"], scores, extra)
        if bracket:
            for stage in ("qf", "sf"):
                for tie in bracket.get(stage) or []:
                    tie["a"] = name_of(tie["a"])["team"]
                    tie["b"] = name_of(tie["b"])["team"]
                    tie["win"] = name_of(tie["win"])["team"] if tie["win"] else None
            if bracket.get("final"):
                fin = bracket["final"]
                fin["a"] = name_of(fin["a"])["team"]
                fin["b"] = name_of(fin["b"])["team"]
                fin["win"] = name_of(fin["win"])["team"] if fin["win"] else None
            bracket["champion"] = (name_of(bracket["champion"])["team"]
                                   if bracket["champion"] else None)
            ko = bracket

    status = "upcoming"
    if any(compute.net_score(by_id[i], mt["group"][0]) is not None
           for i in draw["qualified"] if by_id.get(i)):
        status = "live"
    if ko and ko.get("champion"):
        status = "complete"
    base.update({"status": status, "groups": tables, "ko": ko})
    return base


# ---- differential diamond ---------------------------------------------------

def differential(managers, finished_gws, boot_elements):
    """Best single-GW haul from a starter owned < 7.5%. Ownership uses the
    current bootstrap snapshot (the API doesn't expose historical deadline
    ownership), so the winner is flagged for admin confirmation."""
    best = None
    own = {e["id"]: float(e["selected_by_percent"]) for e in boot_elements}
    web = {e["id"]: e["web_name"] for e in boot_elements}
    by_id = {m["id"]: m for m in managers}
    for gw in finished_gws:
        live = fplapi.event_live(gw)
        if not live:
            continue
        pts = {e["id"]: e["stats"]["total_points"] for e in live["elements"]}
        for m in managers:
            picks = fplapi.entry_picks(m["id"], gw)
            if not picks:
                continue
            for p in picks.get("picks", []):
                if p.get("multiplier", 0) <= 0:
                    continue  # bench / not started
                el = p["element"]
                if own.get(el, 100) >= C.DIFFERENTIAL_OWNERSHIP_MAX:
                    continue
                haul = pts.get(el, 0)
                if best is None or haul > best["haul"]:
                    best = {"team": m["team"], "name": m["name"],
                            "player": web.get(el, "?"), "owned": own.get(el),
                            "haul": haul, "gw": gw}
    return best


# ---- main -------------------------------------------------------------------

def main():
    boot = fplapi.bootstrap()
    gws = gw_state(boot)
    elements = boot["elements"]
    entries = fplapi.all_league_entries(C.LEAGUE_ID)
    print(f"{len(entries)} managers; last finished GW = {gws['last_finished']}")

    managers = []
    for entry_id, team, name in entries:
        managers.append(build_manager(entry_id, team, name,
                                       gws["finished"], elements))
        time.sleep(0.05)
    apply_league_ranks(managers, C.LEAGUE_ID)

    standings = sorted(
        [m for m in managers],
        key=lambda m: (m["rank"] if m["rank"] else 10**9))
    standings_out = [{
        "rank": m["rank"], "last_rank": m["last_rank"], "id": m["id"],
        "team": m["team"], "name": m["name"], "total": m["total"],
        "event_total": m["event_total"],
        "prize": C.OVERALL_PRIZES.get(m["rank"] or 0),
    } for m in standings]

    mts = [build_mini_tournament(mt, managers, gws)
           for mt in C.MINI_TOURNAMENTS]

    lg = gws["last_finished"]
    side = {}
    if gws["started"]:
        top5 = {m["id"] for m in standings[:5]}
        cap = [r for r in compute.captaincy_totals(managers)
               if r["id"] not in top5][:8]
        arr = [r for r in compute.green_arrows(managers, lg)
               if r["id"] not in top5][:8]
        side = {
            "captaincy": cap,
            "arrows": arr,
            "chips": compute.chip_kings(managers),
            "pity": compute.pity(managers, min(lg, C.PITY_LAST_GW),
                                 C.PITY_MAX_HIT),
            "diamond": differential(managers, gws["finished"], elements),
            "comeback": (compute.comeback(managers, C.COMEBACK_SPLIT_GW, lg)[:8]
                         if lg > C.COMEBACK_SPLIT_GW else None),
        }

    out = {
        "meta": {
            "league": C.LEAGUE_NAME, "season": C.SEASON,
            "pool": C.PRIZE_POOL, "entry_fee": C.ENTRY_FEE,
            "managers": len(entries), "expected": C.EXPECTED_MANAGERS,
            "my_id": C.MY_ENTRY_ID,
            "gw": lg, "next_gw": gws["next_id"],
            "next_deadline": gws["next_deadline"],
            "started": gws["started"],
            "updated": os.environ.get("UPDATED_AT", ""),
        },
        "standings": standings_out,
        "mini_tournaments": mts,
        "side": side,
        "overall_prizes": C.OVERALL_PRIZES,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
