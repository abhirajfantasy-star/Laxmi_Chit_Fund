"""League configuration for Laxmi Chit Fund - Season 5.
Edit values here if anything changes; nothing else in the engine hard-codes them.
"""

LEAGUE_ID = 1035071
LEAGUE_NAME = "Laxmi Chit Fund - Season 5"
SEASON = "2026/27"
PRIZE_POOL = "₹1,24,000"
ENTRY_FEE = "₹4,000"
EXPECTED_MANAGERS = 31

# The user's own entry, highlighted on the dashboard.
MY_ENTRY_ID = 427154

# Overall prize table (rank -> prize label), rulebook section 1.
OVERALL_PRIZES = {1: "₹30,000", 2: "₹20,000", 3: "₹12,500",
                  4: "₹7,500", 5: "₹5,000"}

# Mini-tournament calendar (rulebook section 2).
# seed_after = the gameweek whose FINAL standings decide the top-24 + seeding.
# group = the 3 group-stage GWs; ko = the 3 knockout GWs (QF, SF, F).
MINI_TOURNAMENTS = [
    {"id": 1, "seed_after": 2,  "group": [3, 4, 5],    "ko": [6, 7, 8]},
    {"id": 2, "seed_after": 8,  "group": [9, 10, 11],  "ko": [12, 13, 14]},
    {"id": 3, "seed_after": 14, "group": [15, 16, 17], "ko": [18, 19, 20]},
    {"id": 4, "seed_after": 20, "group": [21, 22, 23], "ko": [24, 25, 26]},
    {"id": 5, "seed_after": 26, "group": [27, 28, 29], "ko": [30, 31, 32]},
    {"id": 6, "seed_after": 32, "group": [33, 34, 35], "ko": [36, 37, 38]},
]

MT_QUALIFY_COUNT = 24     # top N of overall table qualify
MT_GROUP_COUNT = 6        # groups
MT_GROUP_SIZE = 4         # per group

DIFFERENTIAL_OWNERSHIP_MAX = 7.5   # % owned, rulebook section 8
PITY_LAST_GW = 30                  # pity tracked up to this GW
PITY_MAX_HIT = 8                   # score disqualified above a -8 hit
COMEBACK_SPLIT_GW = 19             # bottom 21 after this GW; count GW20-38
