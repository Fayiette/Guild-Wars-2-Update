"""Append recent sPvP match details into the long-running CSV log on R2."""

from __future__ import annotations

import csv
import logging
import os
import sys
import time
from pathlib import Path

import requests

from gw2_r2 import (
    configure_logging,
    data_dir,
    discord_user_prefix,
    download_object_or_exit,
    load_repo_env,
    env_required,
    s3_client,
    send_discord_alert,
    upload_file_if_changed,
)

R2_SPVP_MATCH_LOG_CSV_KEY = env_required("R2_SPVP_MATCH_LOG_CSV_KEY")
SCRIPT_LABEL = "GW2 sPvP Export"

GAMES_URL = "https://api.guildwars2.com/v2/pvp/games"
FIELDNAMES = [
    "id", "map_id", "started", "ended", "result", "team",
    "profession", "rating_type", "score_red", "score_blue",
]

logger = logging.getLogger("gw2.spvp")


def _headers() -> dict:
    key = os.getenv("GW2_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def fetch_game_ids() -> list[str]:
    resp = requests.get(GAMES_URL, headers=_headers(), timeout=(5, 20))
    if not resp.ok:
        logger.warning("PvP games index returned status %s.", resp.status_code)
        return []
    return [str(g) for g in resp.json()]


def fetch_game_details(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    resp = requests.get(f"{GAMES_URL}?ids={','.join(ids)}", headers=_headers(), timeout=(5, 30))
    if not resp.ok:
        logger.warning("PvP game details returned status %s.", resp.status_code)
        return []
    return resp.json() or []


def append_new(games: list[dict], path: Path) -> int:
    if not games:
        return 0
    existing_ids: set = set()
    if path.exists():
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                existing_ids.add(row.get("id"))

    appended = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if f.tell() == 0:
            writer.writeheader()
        for game in games:
            gid = str(game.get("id", ""))
            if not gid or gid in existing_ids:
                continue
            scores = game.get("scores") or {}
            writer.writerow({
                "id": gid,
                "map_id": game.get("map_id", ""),
                "started": game.get("started", ""),
                "ended": game.get("ended", ""),
                "result": game.get("result", ""),
                "team": game.get("team", ""),
                "profession": game.get("profession", ""),
                "rating_type": game.get("rating_type", ""),
                "score_red": scores.get("red", ""),
                "score_blue": scores.get("blue", ""),
            })
            existing_ids.add(gid)
            appended += 1
    return appended


def run() -> str:
    if not os.getenv("GW2_API_KEY"):
        logger.error("GW2 API key not configured.")
        return "error"

    work = data_dir()
    local_csv = work / R2_SPVP_MATCH_LOG_CSV_KEY

    client, bucket = s3_client()
    download_object_or_exit(client, bucket, R2_SPVP_MATCH_LOG_CSV_KEY, local_csv)

    ids = fetch_game_ids()
    if not ids:
        return "no-data"

    games = fetch_game_details(ids)
    new_count = append_new(games, local_csv)
    logger.info("Appended %d new match rows.", new_count)

    return upload_file_if_changed(
        client,
        bucket,
        R2_SPVP_MATCH_LOG_CSV_KEY,
        local_csv,
        content_type="text/csv",
        cache_control="max-age=0, no-cache, must-revalidate",
    )


def main() -> int:
    load_repo_env()
    configure_logging()
    ts = int(time.time())
    try:
        result = run()
    except Exception as e:  # noqa: BLE001
        logger.exception("Unhandled error.")
        send_discord_alert(
            f"{discord_user_prefix()}❌ {SCRIPT_LABEL} crashed at <t:{ts}:f>: {type(e).__name__}"
        )
        return 1

    if result == "uploaded":
        send_discord_alert(f"✅ {SCRIPT_LABEL} — Uploaded to R2 at <t:{ts}:f>")
    elif result == "no-change":
        send_discord_alert(f"✅ {SCRIPT_LABEL} — No new matches at <t:{ts}:f>")
    elif result == "no-data":
        send_discord_alert(f"{discord_user_prefix()}⚠️ {SCRIPT_LABEL} — No data at <t:{ts}:f>")
    else:
        send_discord_alert(f"{discord_user_prefix()}❌ {SCRIPT_LABEL} — Result {result} at <t:{ts}:f>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
