"""Append daily PvP standings and refresh PvP seasons metadata (both JSON)."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
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

R2_LEAGUE_STANDINGS_JSON_KEY = env_required("R2_LEAGUE_STANDINGS_JSON_KEY")
R2_LEAGUE_SEASONS_JSON_KEY = env_required("R2_LEAGUE_SEASONS_JSON_KEY")
SCRIPT_LABEL = "GW2 PvP League History"

STANDINGS_URL = "https://api.guildwars2.com/v2/pvp/standings"
SEASONS_URL = "https://api.guildwars2.com/v2/pvp/seasons"

logger = logging.getLogger("gw2.league")


def _headers() -> dict:
    key = os.getenv("GW2_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def fetch_standings():
    if not os.getenv("GW2_API_KEY"):
        return None
    try:
        r = requests.get(STANDINGS_URL, headers=_headers(), timeout=(5, 20))
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        logger.warning("Failed to fetch standings.")
        return None


def load_cached_seasons(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {s["id"]: s for s in data.get("seasons", [])}
    except (OSError, json.JSONDecodeError):
        return {}


def fetch_seasons(seasons_path: Path):
    try:
        r = requests.get(f"{SEASONS_URL}?ids=all", timeout=(5, 30))
        r.raise_for_status()
        seasons_list = r.json() or []
    except requests.RequestException:
        logger.warning("Failed to fetch season index.")
        return None

    cached = load_cached_seasons(seasons_path)
    logger.info("Seasons cached locally: %d / API: %d.", len(cached), len(seasons_list))

    if len(cached) == len(seasons_list):
        return list(cached.values())

    for season in seasons_list:
        sid = season.get("id")
        if sid in cached:
            season["divisions"] = cached[sid].get("divisions", [])
            continue
        try:
            div = requests.get(f"{SEASONS_URL}/{sid}/divisions", timeout=(5, 20))
            season["divisions"] = div.json() if div.status_code == 200 else []
        except requests.RequestException:
            season["divisions"] = []
        time.sleep(0.2)
    return seasons_list


def load_standings_file(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("history", [])
    except (OSError, json.JSONDecodeError):
        return []


def write_standings(path: Path, standings, history: list) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = [h for h in history if h.get("date") != today]
    history.append({
        "date": today,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "standings": standings,
    })
    history.sort(key=lambda h: h.get("date", ""))
    out = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_entries": len(history),
        "current": standings,
        "history": history,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def write_seasons(path: Path, seasons: list) -> None:
    existing = load_cached_seasons(path)
    for s in seasons:
        existing[s["id"]] = s
    out = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_seasons": len(existing),
        "seasons": list(existing.values()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def run() -> str:
    if not os.getenv("GW2_API_KEY"):
        logger.error("GW2 API key not configured.")
        return "error"

    work = data_dir()
    standings_path = work / R2_LEAGUE_STANDINGS_JSON_KEY
    seasons_path = work / R2_LEAGUE_SEASONS_JSON_KEY

    client, bucket = s3_client()
    download_object_or_exit(client, bucket, R2_LEAGUE_STANDINGS_JSON_KEY, standings_path)
    download_object_or_exit(client, bucket, R2_LEAGUE_SEASONS_JSON_KEY, seasons_path)

    standings = fetch_standings()
    seasons = fetch_seasons(seasons_path)
    if standings is None or seasons is None:
        return "no-data"

    history = load_standings_file(standings_path)
    write_standings(standings_path, standings, history)
    write_seasons(seasons_path, seasons)

    r1 = upload_file_if_changed(client, bucket, R2_LEAGUE_STANDINGS_JSON_KEY, standings_path, content_type="application/json")
    r2 = upload_file_if_changed(client, bucket, R2_LEAGUE_SEASONS_JSON_KEY, seasons_path, content_type="application/json")
    logger.info("Standings upload: %s. Seasons upload: %s.", r1, r2)

    if r1 == "error" or r2 == "error":
        return "error"
    if r1 == "uploaded" or r2 == "uploaded":
        return "uploaded"
    return "no-change"


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
        send_discord_alert(f"✅ {SCRIPT_LABEL} — Updated standings & seasons at <t:{ts}:f>")
    elif result == "no-change":
        send_discord_alert(f"✅ {SCRIPT_LABEL} — No changes at <t:{ts}:f>")
    elif result == "no-data":
        send_discord_alert(f"{discord_user_prefix()}⚠️ {SCRIPT_LABEL} — Missing data at <t:{ts}:f>")
    else:
        send_discord_alert(f"{discord_user_prefix()}❌ {SCRIPT_LABEL} — Result {result} at <t:{ts}:f>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
