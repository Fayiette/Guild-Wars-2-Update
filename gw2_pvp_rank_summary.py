"""Refresh the single-row PvP rank summary CSV."""

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

R2_PVP_RANK_CSV_KEY = env_required("R2_PVP_RANK_CSV_KEY")
SCRIPT_LABEL = "GW2 PvP Rank Summary"
API_URL = "https://api.guildwars2.com/v2/pvp/stats"
FIELDNAMES = ["pvp_rank", "pvp_rank_points", "pvp_rank_rollovers", "rank_points_until_100"]

logger = logging.getLogger("gw2.pvp_rank")


def _headers() -> dict:
    key = os.getenv("GW2_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def load_existing(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
            return rows[0] if rows else None
    except OSError:
        return None


def write_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerow(row)


def run() -> str:
    if not os.getenv("GW2_API_KEY"):
        logger.error("GW2 API key not configured.")
        return "error"

    work = data_dir()
    local_csv = work / R2_PVP_RANK_CSV_KEY

    client, bucket = s3_client()
    download_object_or_exit(client, bucket, R2_PVP_RANK_CSV_KEY, local_csv)

    existing = load_existing(local_csv)
    resp = requests.get(API_URL, headers=_headers(), timeout=(5, 20))
    if resp.status_code != 200:
        logger.warning("PvP rank endpoint returned status %s.", resp.status_code)
        if existing:
            write_row(local_csv, existing)
            return upload_file_if_changed(
                client, bucket, R2_PVP_RANK_CSV_KEY, local_csv,
                content_type="text/csv",
                cache_control="max-age=0, no-cache, must-revalidate",
            )
        return "error"

    data = resp.json() or {}
    rank = data.get("pvp_rank", 0)
    points = data.get("pvp_rank_points", 0)
    rollovers = data.get("pvp_rank_rollovers", 0)
    write_row(local_csv, {
        "pvp_rank": rank,
        "pvp_rank_points": points,
        "pvp_rank_rollovers": rollovers,
        "rank_points_until_100": max(0, 400000 - rollovers),
    })

    return upload_file_if_changed(
        client, bucket, R2_PVP_RANK_CSV_KEY, local_csv,
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
        send_discord_alert(f"✅ {SCRIPT_LABEL} — No changes at <t:{ts}:f>")
    else:
        send_discord_alert(f"{discord_user_prefix()}❌ {SCRIPT_LABEL} — Result {result} at <t:{ts}:f>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
