# Guild Wars 2 automation

Seven `gw2_*.py` scripts talk to the public API and sync CSV/JSON with **Cloudflare R2**.

## Setup

`[requirements.txt](requirements.txt)`:

```bash
pip install -r requirements.txt
```

Copy `[.env.example](.env.example)` to `**.env**` in this folder, fill secrets, and keep the `R2_*_KEY` lines (or change the object names there). `[gw2_r2.py](gw2_r2.py)` calls `load_dotenv` on that `.env` when imported.


|                                      |                                       |
| ------------------------------------ | ------------------------------------- |
| **R2 creds + `GW2_API_KEY`**         | Required                              |
| `**R2_*_CSV_KEY` / `R2_*_JSON_KEY**` | Required (defaults in `.env.example`) |
| `**DISCORD_*`, `GW2_DATA_DIR**`      | Optional                              |


Scripts read keys with `**os.getenv` only** — no filenames hardcoded in Python.

**CI logs:** stay generic. **Discord:** private channel but still no raw secrets.

## R2 bootstrap

Each run **downloads** required objects first; missing object or failed download → **exit 1**. Optional JSON caches may be absent on first run.

## Local

```bash
cd "Guild Wars 2"
cp .env.example .env   # edit: R2_*, GW2_API_KEY, etc.
pip install -r requirements.txt
python gw2_wallet.py
```

## GitHub Actions

`[.github/workflows/](.github/workflows/)` — seven scheduled workflows. Each job sets `**defaults.run.working-directory: 'Guild Wars 2'**`, so shell steps match local usage: `**pip install -r requirements.txt**` and `**python gw2_*.py**` with no path prefix. `hashFiles(...)` for the pip cache still uses the repo-relative path `Guild Wars 2/requirements.txt` (GitHub always resolves `hashFiles` from the checkout root).

Workflow steps inject the same `**R2_*_KEY**` values as in `.env.example` via `**env**` (runners have no `.env` file). **prod** secrets: `GW2_API_KEY`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`; optional Discord secrets.


| Workflow                   | Cron (UTC)    | Script                    |
| -------------------------- | ------------- | ------------------------- |
| `gw2-wallet.yml`           | `0 1 `* * *   | `gw2_wallet.py`           |
| `gw2-spvp-export.yml`      | `30 4 * `* *  | `gw2_spvp_export.py`      |
| `gw2-pvp-stats-export.yml` | `0 8 * * `*   | `gw2_pvp_stats_export.py` |
| `gw2-pvp-rank-summary.yml` | `30 11 * * *` | `gw2_pvp_rank_summary.py` |
| `gw2-char-details.yml`     | `0 15 * * *`  | `gw2_char_details.py`     |
| `gw2-armory.yml`           | `30 18 * * *` | `gw2_armory.py`           |
| `gw2-league-history.yml`   | `0 22 * * *`  | `gw2_league_history.py`   |


Each workflow supports `workflow_dispatch`.