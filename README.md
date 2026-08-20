# Laxmi Chit Fund · Season 5 — Live Dashboard

A self-updating dashboard for our FPL mini-league. It pulls the official FPL API,
computes every prize category and the mini-tournament draws, and publishes a live
web page that refreshes itself after every gameweek — no manual work once it's set up.

**You do not need to know any coding or use any commands.** Everything below is
point-and-click on the GitHub website.

---

## One-time setup (about 10 minutes, all clicks)

### 1. Create the repository
1. Go to **github.com** and sign in.
2. Top-right **+** → **New repository**.
3. Repository name: `laxmi-chit-fund` (or anything you like).
4. Choose **Public**.
5. Leave everything else as-is and click **Create repository**.

### 2. Upload these files
1. On your new empty repo page, click **uploading an existing file**
   (or **Add file → Upload files**).
2. On your Mac, open the folder you unzipped from me. **Press `Cmd + Shift + .`**
   (period) in Finder so the hidden **`.github`** folder becomes visible — it's
   required for auto-updates.
3. Select **everything** inside the folder (including `.github`) and drag it into
   the GitHub upload box.
4. Scroll down and click **Commit changes**.

### 3. Let the auto-updater push updates
1. In the repo, go to **Settings → Actions → General**.
2. Scroll to **Workflow permissions**, choose **Read and write permissions**,
   click **Save**. *(This lets the update robot save fresh scores back to the repo.)*

### 4. Turn on the live web page
1. Go to **Settings → Pages**.
2. Under **Source**, choose **Deploy from a branch**.
3. Branch: **main**, folder: **/ (root)** → **Save**.
4. Wait ~1 minute, refresh the page — GitHub shows your live link, like
   `https://YOUR-USERNAME.github.io/laxmi-chit-fund/`. **That's your dashboard.**

### 5. Fill it with live data now (optional)
The dashboard already shows the pre-season roster. To force a data pull right away:
1. Go to the **Actions** tab.
2. If prompted, click **I understand my workflows, enable them**.
3. Click **Update dashboard data** → **Run workflow** → **Run workflow**.
4. After a minute it commits a fresh `data.json` and the page updates.

From here on it runs on its own — every 2 hours it checks the FPL API and updates
the page if anything changed. During gameweeks you'll see scores move automatically.

---

## What updates automatically vs. what's set-and-forget

- **Automatic, every 2 hours:** overall table, Chip Kings, Captaincy King,
  Green Arrow King, Differential Diamond, The Comeback, Pity the Living Dead,
  and every mini-tournament group table + knockout bracket.
- **Mini-tournament draws:** generated once, automatically, right after the
  qualifying gameweek, using a reproducible seeded pot draw, then locked into
  `draws/mtN.json` so they never change. To re-draw one, delete that file.
- **FPL League Cup:** appears automatically once FPL seeds it (~GW17).

## Two prizes that need a human eye

The rulebook itself leaves these to the admins — the dashboard flags them:

- **Pity the Living Dead:** auto-applies the "no worse than −8 hit" rule, but the
  valid-XI and active-manager checks are admin calls, so the candidate is shown as
  *pending admin validity check*.
- **Differential Diamond:** the FPL API only exposes *current* ownership, not the
  ownership at each past deadline, so the shown pick is flagged *admin-confirmed*.

## Changing anything

Open **`config.py`** on GitHub (click the file → pencil icon → edit → commit).
It holds the league ID, prize amounts, and the mini-tournament calendar. Nothing
else hard-codes those values.

## Files in this repo

| File | What it is |
|------|------------|
| `index.html` | the dashboard page (what visitors see) |
| `data.json` | the latest computed data (rewritten by the robot) |
| `engine.py` | pulls the FPL API and computes everything |
| `compute.py` | the pure scoring/tournament logic |
| `fplapi.py` | the FPL API client |
| `config.py` | league settings you can edit |
| `draws/` | locked mini-tournament group draws |
| `snapshots/` | player-ownership snapshots near each deadline (Differential Diamond) |
| `cache/` | finalized-gameweek data, so the engine never refetches old weeks |
| `.github/workflows/update.yml` | the every-2-hours auto-updater |
| `tests/` | offline tests for the scoring logic |

The dashboard has a tab per prize, a proper overall table, tap-a-manager profile
cards with rank/points charts, and a light/dark toggle (◑, top-right).
