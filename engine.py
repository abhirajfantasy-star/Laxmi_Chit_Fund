"""Laxmi Chit Fund - Season 5 : data engine (v2).

Pulls the live FPL API, computes every prize category and the mini-tournament
draws, and writes data.json for the dashboard. Designed to run on GitHub
Actions after each gameweek. Safe to run pre-season.

v2 adds:
  * per-manager per-GW history series (powers profile cards + charts)
  * ownership snapshots near each deadline (accurate Differential Diamond)
  * incremental caching of finalized gameweeks (no full refetch every run)

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

HERE = os.path.dirname(__file__)
DRAWS_DIR = os.path.join(HERE, "draws")
CACHE_DIR = os.path.join(HERE, "cache")
SNAP_DIR = os.path.join(HERE, "snapshots")
OUT = os.path.join(HERE, "data.json")
CHIP_KEYS = {"3xc", "bboost", "wildcard", "freehit"}


def _load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False)


# ---- gameweek state ---------------------------------------------------------

def gw_state(boot):
    events = boot["events"]
    finished = [e["id"] for e in events if e["finished"] and e["data_checked"]]
    current = next((e["id"] for e in events if e["is_current"]), None)
    nxt = next((e for e in events if e["is_next"]), None)
    last_finished = max(finished) if finished else 0
    return {"finished": finished, "last_finished": last_finished,
            "current": current, "next_id": nxt["id"] if nxt else None,
            "next_deadline": nxt["deadline_time"] if nxt else None,
            "started": last_finished > 0}


# ---- ownership snapshots ----------------------------------------------------

def snapshot_ownership(elements, gws):
    """Record current ownership for the NEXT gameweek. Overwrites each run
    until that GW is finished, so the final write is the closest we can get to
    the deadline snapshot the rulebook wants. Frozen once the GW completes."""
    nxt = gws["next_id"]
    if not nxt or nxt in gws["finished"]:
        return
    path = os.path.join(SNAP_DIR, f"gw{nxt}.json")
    own = {str(e["id"]): float(e["selected_by_percent"]) for e in elements}
    _save(path, own)


def ownership_for(gw, elements):
    snap = _load(os.path.join(SNAP_DIR, f"gw{gw}.json"))
    if snap:
        return {int(k): v for k, v in snap.items()}
    return {e["id"]: float(e["selected_by_percent"]) for e in elements}


# ---- per-gameweek bundle (cached once a GW is finalized) --------------------

def gw_bundle(gw, entries, is_final):
    """For one GW, return {entry_id: {cap_el, cap_pts, mult, cap_name,
    starters:[[el,pts],...]}}. Cached to cache/gwN.json when the GW is final,
    so later runs skip the picks/live refetch entirely."""
    cpath = os.path.join(CACHE_DIR, f"gw{gw}.json")
    if is_final:
        cached = _load(cpath)
        if cached:
            return {int(k): v for k, v in cached.items()}
    live = fplapi.event_live(gw) or {"elements": []}
    pts = {e["id"]: e["stats"]["total_points"] for e in live["elements"]}
    bundle = {}
    for entry_id, _, _ in entries:
        picks = fplapi.entry_picks(entry_id, gw)
        if not picks:
            continue
        cap = next((p for p in picks.get("picks", []) if p.get("is_captain")), None)
        starters = [[p["element"], pts.get(p["element"], 0)]
                    for p in picks.get("picks", []) if p.get("multiplier", 0) > 0]
        bundle[entry_id] = {
            "cap_el": cap["element"] if cap else None,
            "cap_pts": pts.get(cap["element"], 0) if cap else 0,
            "mult": max(cap.get("multiplier", 2), 2) if cap else 2,
            "starters": starters,
        }
    if is_final:
        _save(cpath, {str(k): v for k, v in bundle.items()})
    return bundle


# ---- build one manager record ----------------------------------------------

def build_manager(entry_id, team, name):
    rec = {"id": entry_id, "team": team, "name": name,
           "total": None, "rank": None, "last_rank": None, "event_total": None,
           "history": {}, "captain": {}, "series": []}
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
                "chip": chips_by_gw.get(gw)}
        if hist.get("current"):
            rec["total"] = hist["current"][-1]["total_points"]
    return rec


def attach_captain(rec, gw, bundle, web):
    b = bundle.get(rec["id"])
    if not b or not b.get("cap_el"):
        return
    rec["captain"][gw] = {"pts": b["cap_pts"], "mult": b["mult"],
                          "element": b["cap_el"]}
    rec.setdefault("_capname", {})[gw] = web.get(b["cap_el"], "?")


def build_series(rec, finished_gws):
    """Compact per-GW series for the profile card + charts."""
    out = []
    for gw in sorted(rec["history"]):
        h = rec["history"][gw]
        cap = rec["captain"].get(gw, {})
        out.append({"gw": gw, "net": h["net"], "total": None,
                    "orank": h["overall_rank"], "chip": h.get("chip"),
                    "cap_pts": cap.get("pts", 0) * cap.get("mult", 2) if cap else None,
                    "cap": rec.get("_capname", {}).get(gw),
                    "bench": h["bench"]})
    run = 0
    for row in out:
        run += row["net"]
        row["total"] = run
    rec["series"] = out


# ---- overall standings ------------------------------------------------------

def apply_league_ranks(managers, league_id):
    data = fplapi.league_standings(league_id, 1)
    by_id = {m["id"]: m for m in managers}
    results = (data or {}).get("standings", {}).get("results", [])
    for r in results:
        m = by_id.get(r["entry"])
        if m:
            m["rank"] = r["rank"]; m["last_rank"] = r["last_rank"]
            m["total"] = r["total"]; m["event_total"] = r["event_total"]
    if not results:
        for i, m in enumerate(sorted(managers,
                                     key=lambda x: -(x["total"] or 0)), 1):
            m["rank"] = i if m["total"] else None
    return managers


# ---- mini-tournament draws --------------------------------------------------

def draw_seed(ranked_pairs):
    blob = json.dumps(ranked_pairs, sort_keys=True).encode()
    return int(hashlib.sha256(blob).hexdigest()[:12], 16)


def load_or_create_draw(mt, managers, gws):
    path = os.path.join(DRAWS_DIR, f"mt{mt['id']}.json")
    existing = _load(path)
    if existing:
        return existing
    if mt["seed_after"] not in gws["finished"]:
        return None
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
    _save(path, draw)
    return draw


def build_mini_tournament(mt, managers, gws):
    by_id = {m["id"]: m for m in managers}
    draw = load_or_create_draw(mt, managers, gws)
    base = {"id": mt["id"], "window": f"GW{mt['group'][0]}–GW{mt['ko'][-1]}",
            "group_gws": mt["group"], "ko_gws": mt["ko"], "status": "upcoming",
            "seed": draw["seed"] if draw else None, "groups": None, "ko": None}
    if not draw:
        return base
    scores = {i: {gw: compute.net_score(by_id[i], gw)
                  for gw in mt["group"] + mt["ko"] if by_id.get(i)}
              for i in draw["qualified"]}
    hits = {i: sum(by_id[i]["history"].get(gw, {}).get("hit", 0)
                   for gw in mt["group"]) for i in draw["qualified"] if by_id.get(i)}

    def nm(i):
        m = by_id.get(i, {})
        return {"id": i, "team": m.get("team", "?"), "name": m.get("name", "?")}

    tables = []
    for gi, grp in enumerate(draw["groups"]):
        tab = compute.group_table(grp, mt["group"], scores, hits)
        for r in tab:
            r.update(nm(r["id"]))
        tables.append({"group": "ABCDEF"[gi], "rows": tab})

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
            s.update(nm(s["id"]))
        bracket = compute.run_bracket(seeds, mt["ko"], scores, extra)
        if bracket:
            for stage in ("qf", "sf"):
                for tie in bracket.get(stage) or []:
                    tie["a"] = nm(tie["a"])["team"]; tie["b"] = nm(tie["b"])["team"]
                    tie["win"] = nm(tie["win"])["team"] if tie["win"] else None
            if bracket.get("final"):
                fin = bracket["final"]
                fin["a"] = nm(fin["a"])["team"]; fin["b"] = nm(fin["b"])["team"]
                fin["win"] = nm(fin["win"])["team"] if fin["win"] else None
            bracket["champion"] = (nm(bracket["champion"])["team"]
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

def differential(managers, finished_gws, elements, bundles):
    best = None
    web = {e["id"]: e["web_name"] for e in elements}
    by_id = {m["id"]: m for m in managers}
    for gw in finished_gws:
        own = ownership_for(gw, elements)
        b = bundles.get(gw, {})
        for m in managers:
            mb = b.get(m["id"])
            if not mb:
                continue
            for el, p in mb["starters"]:
                if own.get(el, 100) >= C.DIFFERENTIAL_OWNERSHIP_MAX:
                    continue
                if best is None or p > best["haul"]:
                    best = {"team": m["team"], "name": m["name"],
                            "player": web.get(el, "?"), "owned": own.get(el),
                            "haul": p, "gw": gw}
    return best


# ---- main -------------------------------------------------------------------

def main():
    boot = fplapi.bootstrap()
    gws = gw_state(boot)
    elements = boot["elements"]
    web = {e["id"]: e["web_name"] for e in elements}
    snapshot_ownership(elements, gws)
    entries = fplapi.all_league_entries(C.LEAGUE_ID)
    print(f"{len(entries)} managers; last finished GW = {gws['last_finished']}")

    # per-GW bundles (cached once final) -> captain + starters
    bundles = {}
    for gw in gws["finished"]:
        bundles[gw] = gw_bundle(gw, entries, is_final=True)
    if gws["current"] and gws["current"] not in gws["finished"]:
        bundles[gws["current"]] = gw_bundle(gws["current"], entries, is_final=False)

    managers = []
    for entry_id, team, name in entries:
        rec = build_manager(entry_id, team, name)
        for gw, b in bundles.items():
            attach_captain(rec, gw, b, web)
        build_series(rec, gws["finished"])
        managers.append(rec)
        time.sleep(0.02)
    apply_league_ranks(managers, C.LEAGUE_ID)

    standings = sorted(managers, key=lambda m: (m["rank"] or 10**9))
    standings_out = [{
        "rank": m["rank"], "last_rank": m["last_rank"], "id": m["id"],
        "team": m["team"], "name": m["name"], "total": m["total"],
        "event_total": m["event_total"], "prize": C.OVERALL_PRIZES.get(m["rank"] or 0),
        "series": m["series"],
        "chips_used": sorted({h["chip"] for h in m["history"].values() if h.get("chip")}),
    } for m in standings]

    mts = [build_mini_tournament(mt, managers, gws) for mt in C.MINI_TOURNAMENTS]

    lg = gws["last_finished"]
    side = {}
    if gws["started"]:
        top5 = {m["id"] for m in standings[:5]}
        side = {
            "captaincy": [r for r in compute.captaincy_totals(managers)
                          if r["id"] not in top5][:12],
            "arrows": [r for r in compute.green_arrows(managers, lg)
                       if r["id"] not in top5][:12],
            "chips": compute.chip_kings(managers),
            "pity": compute.pity(managers, min(lg, C.PITY_LAST_GW), C.PITY_MAX_HIT),
            "diamond": differential(managers, gws["finished"], elements, bundles),
            "comeback": (compute.comeback(managers, C.COMEBACK_SPLIT_GW, lg)[:12]
                         if lg > C.COMEBACK_SPLIT_GW else None),
        }

    out = {
        "meta": {"league": C.LEAGUE_NAME, "season": C.SEASON, "pool": C.PRIZE_POOL,
                 "entry_fee": C.ENTRY_FEE, "managers": len(entries),
                 "expected": C.EXPECTED_MANAGERS, "my_id": C.MY_ENTRY_ID,
                 "gw": lg, "next_gw": gws["next_id"],
                 "next_deadline": gws["next_deadline"], "started": gws["started"],
                 "updated": os.environ.get("UPDATED_AT", "")},
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
