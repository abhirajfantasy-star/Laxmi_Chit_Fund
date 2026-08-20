"""Pure scoring/tournament logic for Laxmi Chit Fund - Season 5.

No network here on purpose: every function takes plain dicts/lists so it can be
unit-tested offline. The engine builds those inputs from the FPL API.

Manager records passed in look like:
    {
      "id": int, "team": str, "name": str,
      "total": int, "rank": int, "last_rank": int,
      "history": { gw(int): {"net": int, "gross": int, "hit": int,
                             "bench": int, "overall_rank": int|None,
                             "chip": str|None} },
      "captain": { gw(int): {"pts": int, "mult": int} },  # pts = raw element pts
    }
"""
import random

# ---- gameweek helpers -------------------------------------------------------


def net_score(mgr, gw):
    """Net FPL score for a manager in a GW, or None if not played."""
    h = mgr["history"].get(gw)
    return None if h is None else h["net"]


def played(mgr, gw):
    return mgr["history"].get(gw) is not None


# ---- mini-tournament: seeded pot draw --------------------------------------


def draw_groups(ranked_ids, seed, n_groups=6, group_size=4):
    """Champions-League-style seeded draw. `ranked_ids` are the qualifiers best
    -> worst. Split into `group_size` pots of `n_groups`, shuffle each pot with
    a fixed seed, then place one team from each pot into every group. Fully
    reproducible: same ranked_ids + seed always yields identical groups.
    Returns a list of groups, each a list of ids (pot order preserved).
    """
    need = n_groups * group_size
    ids = list(ranked_ids)[:need]
    rng = random.Random(seed)
    pots = [ids[i * n_groups:(i + 1) * n_groups] for i in range(group_size)]
    for p in pots:
        rng.shuffle(p)
    groups = [[] for _ in range(n_groups)]
    for p in pots:
        for gi in range(n_groups):
            if gi < len(p):
                groups[gi].append(p[gi])
    return groups


def round_robin(group):
    """Fixtures for a 4-team group over 3 rounds. group = [a,b,c,d] (ids).
    Returns list of 3 rounds, each a list of (home, away) id pairs."""
    a, b, c, d = group
    return [[(a, b), (c, d)], [(a, c), (b, d)], [(a, d), (b, c)]]


def group_table(group, group_gws, scores, hits):
    """Compute a group's H2H table.
    scores[id][gw] -> net score (missing => not yet played).
    hits[id] -> total transfer-hit points taken across the group GWs.
    Tiebreakers (rulebook): H2H points, then total FPL pts in the group GWs,
    then fewest hit points.
    """
    rows = {i: {"id": i, "w": 0, "d": 0, "l": 0, "pts": 0, "gf": 0,
                "hits": hits.get(i, 0), "played": 0} for i in group}
    for rnd_idx, rnd in enumerate(round_robin(group)):
        if rnd_idx >= len(group_gws):
            break
        gw = group_gws[rnd_idx]
        for x, y in rnd:
            sx = scores.get(x, {}).get(gw)
            sy = scores.get(y, {}).get(gw)
            if sx is None or sy is None:
                continue
            rows[x]["played"] += 1
            rows[y]["played"] += 1
            if sx > sy:
                rows[x]["w"] += 1; rows[x]["pts"] += 3; rows[y]["l"] += 1
            elif sy > sx:
                rows[y]["w"] += 1; rows[y]["pts"] += 3; rows[x]["l"] += 1
            else:
                rows[x]["d"] += 1; rows[y]["d"] += 1
                rows[x]["pts"] += 1; rows[y]["pts"] += 1
    # gf = total net pts across all group GWs played so far
    for i in group:
        rows[i]["gf"] = sum((scores.get(i, {}).get(gw) or 0)
                            for gw in group_gws)
    ordered = sorted(rows.values(),
                     key=lambda r: (-r["pts"], -r["gf"], r["hits"]))
    return ordered


def pick_advancers(tables):
    """From a list of ordered group tables, return the 8 knockout qualifiers:
    the 6 group winners plus the 2 best runners-up (by H2H pts, then gf)."""
    winners = [t[0] for t in tables if t]
    runners = [t[1] for t in tables if len(t) > 1]
    best_ru = sorted(runners, key=lambda r: (-r["pts"], -r["gf"]))[:2]
    winners_seeded = sorted(winners, key=lambda r: (-r["pts"], -r["gf"]))
    return winners_seeded + best_ru  # 8 seeds, strongest first


def knockout_winner(a, b, gw, scores, extra):
    """Decide one knockout tie between ids a and b in `gw`.
    extra[id][gw] = {"cap": int, "bench": int, "bb": bool, "rank": int}.
    Returns (winner_id, sa, sb) or (None,...) if not yet played."""
    sa = scores.get(a, {}).get(gw)
    sb = scores.get(b, {}).get(gw)
    if sa is None or sb is None:
        return None, sa, sb
    if sa != sb:
        return (a if sa > sb else b), sa, sb
    ea = extra.get(a, {}).get(gw, {})
    eb = extra.get(b, {}).get(gw, {})
    # (1) captain points
    if ea.get("cap", 0) != eb.get("cap", 0):
        return (a if ea["cap"] > eb["cap"] else b), sa, sb
    # (2) bench points (skipped in a Bench Boost week)
    if not (ea.get("bb") or eb.get("bb")):
        if ea.get("bench", 0) != eb.get("bench", 0):
            return (a if ea["bench"] > eb["bench"] else b), sa, sb
    # (3) higher overall league rank going into the GW (smaller = better)
    ra, rb = ea.get("rank", 1e9), eb.get("rank", 1e9)
    if ra != rb:
        return (a if ra < rb else b), sa, sb
    return None, sa, sb  # dead heat -> admin coin toss


def run_bracket(seeds, ko_gws, scores, extra):
    """Play a straight 8-seed bracket over 3 GWs. seeds strongest-first.
    Bracket pairing: 1v8, 4v5, 2v7, 3v6.
    Returns {"qf":[...], "sf":[...], "final":..., "champion":id|None}."""
    def tie(x, y, gw):
        w, sx, sy = knockout_winner(x, y, gw, scores, extra)
        return {"a": x, "b": y, "sa": sx, "sb": sy, "win": w}
    ids = [s["id"] for s in seeds]
    if len(ids) < 8:
        return None
    qf_gw, sf_gw, f_gw = ko_gws
    qf = [tie(ids[0], ids[7], qf_gw), tie(ids[3], ids[4], qf_gw),
          tie(ids[1], ids[6], qf_gw), tie(ids[2], ids[5], qf_gw)]
    sf, final, champ = [], None, None
    if all(m["win"] for m in qf):
        sf = [tie(qf[0]["win"], qf[1]["win"], sf_gw),
              tie(qf[2]["win"], qf[3]["win"], sf_gw)]
        if all(m["win"] for m in sf):
            final = tie(sf[0]["win"], sf[1]["win"], f_gw)
            champ = final["win"]
    return {"qf": qf, "sf": sf, "final": final, "champion": champ}


# ---- season-long side prizes ------------------------------------------------


def captaincy_totals(managers):
    """Total captain points after multipliers per manager."""
    out = []
    for m in managers:
        tot = sum(c["pts"] * c["mult"] for c in m["captain"].values())
        out.append({"id": m["id"], "team": m["team"], "name": m["name"],
                    "pts": tot})
    return sorted(out, key=lambda r: -r["pts"])


def green_arrows(managers, upto_gw):
    """Count GWs where overall rank improved vs the prior GW.
    Tiebreak: largest total rank improvement."""
    out = []
    for m in managers:
        arrows, improvement = 0, 0
        prev = None
        for gw in range(1, upto_gw + 1):
            h = m["history"].get(gw)
            if not h or h.get("overall_rank") is None:
                continue
            r = h["overall_rank"]
            if prev is not None and r < prev:
                arrows += 1
                improvement += (prev - r)
            prev = r
        out.append({"id": m["id"], "team": m["team"], "name": m["name"],
                    "n": arrows, "improvement": improvement})
    return sorted(out, key=lambda r: (-r["n"], -r["improvement"]))


def chip_kings(managers):
    """Best single-GW return per chip. TC counts only the tripled captain
    (raw x3); BB/WC/FH count the whole net team score that week."""
    chip_map = {"3xc": "Triple Captain", "bboost": "Bench Boost",
                "wildcard": "Wildcard", "freehit": "Free Hit"}
    best = {v: None for v in chip_map.values()}
    for m in managers:
        for gw, h in m["history"].items():
            chip = h.get("chip")
            if chip not in chip_map:
                continue
            label = chip_map[chip]
            if chip == "3xc":
                cap = m["captain"].get(gw)
                score = (cap["pts"] * 3) if cap else h["net"]
            else:
                score = h["net"]
            cur = best[label]
            if cur is None or score > cur["score"]:
                best[label] = {"team": m["team"], "name": m["name"],
                               "score": score, "gw": gw}
    return best


def pity(managers, last_gw, max_hit):
    """Lowest net GW score up to `last_gw`. Auto-checks the hit rule
    (<= max_hit); XI-validity and active-manager are left for admins.
    Returns the auto-lowest candidate and flags for review."""
    cand = None
    for m in managers:
        for gw in range(1, last_gw + 1):
            h = m["history"].get(gw)
            if not h:
                continue
            auto_ok = h["hit"] <= max_hit
            row = {"id": m["id"], "team": m["team"], "name": m["name"],
                   "score": h["net"], "gw": gw, "hit": h["hit"], "auto_ok": auto_ok}
            if auto_ok and (cand is None or h["net"] < cand["score"]):
                cand = row
    return cand


def comeback(managers, split_gw, last_gw):
    """Most net points GW(split_gw+1)..last_gw among the bottom 21 after
    split_gw. Only meaningful once split_gw is scored."""
    ranked = sorted(managers, key=lambda m: m["rank"])
    bottom = ranked[10:]  # ranks 11..31
    out = []
    for m in bottom:
        pts = sum(net_score(m, gw) or 0
                  for gw in range(split_gw + 1, last_gw + 1))
        out.append({"id": m["id"], "team": m["team"], "name": m["name"],
                    "pts": pts})
    return sorted(out, key=lambda r: -r["pts"])
