"""Offline tests for the pure scoring logic. Run: python tests/test_compute.py
Uses synthetic, FPL-API-shaped data so it needs no network."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import compute


def mk(mid, gw_net, caps=None, ranks=None, chips=None, bench=None, hits=None):
    """Build a manager record from simple per-gw dicts."""
    hist = {}
    for gw, net in gw_net.items():
        hist[gw] = {"net": net, "gross": net + (hits or {}).get(gw, 0),
                    "hit": (hits or {}).get(gw, 0),
                    "bench": (bench or {}).get(gw, 0),
                    "overall_rank": (ranks or {}).get(gw),
                    "chip": (chips or {}).get(gw)}
    cap = {gw: {"pts": p, "mult": 2} for gw, p in (caps or {}).items()}
    return {"id": mid, "team": f"T{mid}", "name": f"N{mid}", "rank": mid,
            "total": sum(gw_net.values()), "history": hist, "captain": cap}


def test_draw_reproducible():
    ids = list(range(1, 25))
    g1 = compute.draw_groups(ids, seed=12345)
    g2 = compute.draw_groups(ids, seed=12345)
    assert g1 == g2, "same seed must reproduce the draw"
    assert len(g1) == 6 and all(len(g) == 4 for g in g1)
    flat = [x for g in g1 for x in g]
    assert sorted(flat) == ids, "every qualifier placed exactly once"
    # pot integrity: each group has one team from each pot of 6
    pots = [set(ids[i*6:(i+1)*6]) for i in range(4)]
    for g in g1:
        assert all(len({x} & p) for x, p in zip(g, pots)), "one per pot"
    print("PASS draw reproducible + pot-balanced")


def test_group_table():
    # group of 4, 3 GWs. Build scores so A wins all, B second.
    A, B, Cc, D = 1, 2, 3, 4
    grp = [A, B, Cc, D]
    gws = [1, 2, 3]
    # round1: A-B, C-D ; round2: A-C, B-D ; round3: A-D, B-C
    scores = {
        A: {1: 80, 2: 80, 3: 80},   # A beats everyone
        B: {1: 50, 2: 60, 3: 70},   # loses to A, beats D&C
        Cc: {1: 40, 2: 40, 3: 40},
        D: {1: 45, 2: 30, 3: 30},
    }
    tab = compute.group_table(grp, gws, scores, hits={})
    assert tab[0]["id"] == A and tab[0]["pts"] == 9, tab
    assert tab[0]["w"] == 3
    # B should be 2nd (beat C and D)
    assert tab[1]["id"] == B, tab
    print("PASS group table + ordering")


def test_group_tiebreak_by_points():
    # Teams 1 and 2 both finish 2-0-1 (6 pts); higher total FPL pts breaks it.
    # Fixtures: gw1 (1v2),(3v4) ; gw2 (1v3),(2v4) ; gw3 (1v4),(2v3)
    grp = [1, 2, 3, 4]
    gws = [1, 2, 3]
    scores = {
        1: {1: 55, 2: 70, 3: 70},   # loses to 2, beats 3 & 4  -> gf 195
        2: {1: 60, 2: 70, 3: 40},   # beats 1 & 4, loses to 3   -> gf 170
        3: {1: 20, 2: 20, 3: 50},
        4: {1: 25, 2: 25, 3: 25},
    }
    tab = compute.group_table(grp, gws, scores, hits={})
    assert tab[0]["pts"] == 6 and tab[1]["pts"] == 6, tab
    assert tab[0]["id"] == 1 and tab[1]["id"] == 2, tab  # gf breaks the tie
    assert tab[0]["gf"] > tab[1]["gf"]
    print("PASS group tiebreak by total points")


def test_knockout_tiebreak_captain():
    scores = {1: {6: 70}, 2: {6: 70}}  # level on net
    extra = {1: {6: {"cap": 20, "bench": 5, "bb": False, "rank": 3}},
             2: {6: {"cap": 12, "bench": 9, "bb": False, "rank": 1}}}
    w, sa, sb = compute.knockout_winner(1, 2, 6, scores, extra)
    assert w == 1, "higher captain points should win the tie"
    print("PASS knockout captain tiebreak")


def test_bracket_full():
    seeds = [{"id": i} for i in range(1, 9)]
    ko = [6, 7, 8]
    # deterministic scores: lower id always scores higher
    scores = {i: {g: 100 - i for g in ko} for i in range(1, 9)}
    extra = {i: {g: {} for g in ko} for i in range(1, 9)}
    br = compute.run_bracket(seeds, ko, scores, extra)
    assert br["champion"] == 1, br
    assert len(br["qf"]) == 4 and len(br["sf"]) == 2 and br["final"]
    print("PASS full bracket resolves champion")


def test_chip_kings():
    mgrs = [
        mk(1, {5: 60, 8: 90}, caps={8: 15}, chips={5: "wildcard", 8: "3xc"}),
        mk(2, {5: 88}, chips={5: "bboost"}),
    ]
    ck = compute.chip_kings(mgrs)
    assert ck["Wildcard"]["score"] == 60
    assert ck["Bench Boost"]["score"] == 88
    assert ck["Triple Captain"]["score"] == 45, ck  # cap 15 x3
    print("PASS chip kings incl. triple-captain x3")


def test_captaincy_and_arrows():
    mgrs = [
        mk(1, {1: 50, 2: 60, 3: 40}, caps={1: 10, 2: 12, 3: 8},
           ranks={1: 500000, 2: 300000, 3: 350000}),
        mk(2, {1: 40, 2: 40, 3: 40}, caps={1: 5, 2: 5, 3: 5},
           ranks={1: 900000, 2: 800000, 3: 700000}),
    ]
    cap = compute.captaincy_totals(mgrs)
    assert cap[0]["id"] == 1 and cap[0]["pts"] == (10+12+8)*2, cap
    arr = compute.green_arrows(mgrs, 3)
    a1 = next(a for a in arr if a["id"] == 1)
    a2 = next(a for a in arr if a["id"] == 2)
    assert a1["n"] == 1, a1   # improved only GW2 (500k->300k), worse GW3
    assert a2["n"] == 2, a2   # improved GW2 and GW3
    print("PASS captaincy totals + green arrows")


def test_pity_hit_rule():
    mgrs = [
        mk(1, {1: 20}, hits={1: 12}),  # lowest but -12 hit -> disqualified
        mk(2, {1: 28}, hits={1: 0}),   # valid low
        mk(3, {1: 55}),
    ]
    p = compute.pity(mgrs, last_gw=30, max_hit=8)
    assert p["name"] == "N2" and p["score"] == 28, p
    print("PASS pity respects the -8 hit rule")


def test_comeback():
    # 12 managers ranked 1..12; bottom (rank>=11) counts GW20+
    mgrs = []
    for i in range(1, 13):
        m = mk(i, {20: i, 21: i})
        m["rank"] = i
        mgrs.append(m)
    cb = compute.comeback(mgrs, split_gw=19, last_gw=21)
    # only ranks 11,12 eligible here (bottom slice starts at index 10)
    ids = {r["id"] for r in cb}
    assert ids == {11, 12}, ids
    assert cb[0]["id"] == 12  # highest score
    print("PASS comeback eligibility + scoring")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} tests passed.")
