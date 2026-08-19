"""Thin client for the public Fantasy Premier League API.

Runs on GitHub Actions (which has open internet). The FPL API rejects requests
with no browser User-Agent, so we always send one. Responses are cached on disk
for the duration of a run to avoid hammering the API when several categories
need the same manager's data.
"""
import json
import os
import time
import urllib.request
import urllib.error

BASE = "https://fantasy.premierleague.com/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_CACHE = {}


def get_json(path, retries=4, pause=1.5):
    """GET {BASE}/{path} and parse JSON, with retries and an in-run cache."""
    if path in _CACHE:
        return _CACHE[path]
    url = f"{BASE}/{path}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
                _CACHE[path] = data
                return data
        except urllib.error.HTTPError as e:
            last = e
            # 404 for a manager/gw that doesn't exist yet is not retryable.
            if e.code == 404:
                _CACHE[path] = None
                return None
            time.sleep(pause * (attempt + 1))
        except Exception as e:  # noqa: BLE001 - network flakiness, retry
            last = e
            time.sleep(pause * (attempt + 1))
    raise RuntimeError(f"FPL API failed for {url}: {last}")


def bootstrap():
    return get_json("bootstrap-static/")


def league_standings(league_id, page=1):
    return get_json(f"leagues-classic/{league_id}/standings/"
                    f"?page_standings={page}")


def all_league_entries(league_id):
    """Return every manager in the league across both standings and,
    before kickoff, the new_entries list. Each item: (entry, entry_name,
    player_name)."""
    seen, out = set(), []
    page = 1
    while True:
        data = league_standings(league_id, page)
        if not data:
            break
        for r in data.get("standings", {}).get("results", []):
            if r["entry"] not in seen:
                seen.add(r["entry"])
                out.append((r["entry"], r["entry_name"], r["player_name"]))
        # pre-season: managers live in new_entries until GW1 is scored
        for r in data.get("new_entries", {}).get("results", []):
            if r["entry"] not in seen:
                seen.add(r["entry"])
                name = f"{r['player_first_name']} {r['player_last_name']}".strip()
                out.append((r["entry"], r["entry_name"], name))
        if not data.get("standings", {}).get("has_next"):
            break
        page += 1
    return out


def entry_history(entry_id):
    """Per-GW history + chips for a manager. None before they've played."""
    return get_json(f"entry/{entry_id}/history/")


def entry_picks(entry_id, gw):
    """Squad picks for a manager in one GW (captain, multipliers, chip)."""
    return get_json(f"entry/{entry_id}/event/{gw}/picks/")


def event_live(gw):
    """Per-player points for one GW (used for captain + differential)."""
    return get_json(f"event/{gw}/live/")


def entry_cup(entry_id):
    """Cup match history for a manager (League Cup)."""
    return get_json(f"entry/{entry_id}/cup/")
