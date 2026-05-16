"""Fetch GW2 account wallet, merge into local CSV, sync with R2.

Public-CI safe: no account-identifying data, secrets, or URLs are printed.
"""

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

R2_WALLET_CSV_KEY = env_required("R2_WALLET_CSV_KEY")
SCRIPT_LABEL = "GW2 Wallet"

WALLET_URL = "https://api.guildwars2.com/v2/account/wallet"
CURRENCIES_URL = "https://api.guildwars2.com/v2/currencies"
FIELDNAMES = ["currency_id", "name", "amount", "description", "icon"]

logger = logging.getLogger("gw2.wallet")


def _api_headers() -> dict:
    key = os.getenv("GW2_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def load_existing(csv_path: Path) -> dict:
    existing: dict = {}
    if not csv_path.exists():
        return existing
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                cid = row.get("currency_id")
                if cid:
                    existing[cid] = row
        logger.info("Loaded %d existing rows from local CSV.", len(existing))
    except OSError:
        logger.warning("Failed to read existing CSV.")
    return existing


def fetch_wallet() -> list:
    resp = requests.get(WALLET_URL, headers=_api_headers(), timeout=(5, 20))
    if not resp.ok:
        logger.warning("Wallet endpoint returned status %s.", resp.status_code)
        return []
    return resp.json() or []


def fetch_currency_metadata(ids: list[int]) -> dict:
    if not ids:
        return {}
    ids_str = ",".join(str(i) for i in ids)
    resp = requests.get(f"{CURRENCIES_URL}?ids={ids_str}", timeout=(5, 20))
    if not resp.ok:
        logger.warning("Currencies endpoint returned status %s.", resp.status_code)
        return {}
    return {c["id"]: c for c in resp.json()}


def write_csv(path: Path, wallet: list, metadata: dict, existing: dict) -> None:
    new_rows: dict = {}
    for item in wallet:
        cid = item.get("id")
        if cid is None:
            continue
        meta = metadata.get(cid, {})
        new_rows[str(cid)] = {
            "currency_id": cid,
            "name": meta.get("name", "Unknown"),
            "amount": item.get("value", 0),
            "description": meta.get("description", ""),
            "icon": meta.get("icon", ""),
        }

    merged: dict = {}
    for cid in set(new_rows) | set(existing):
        merged[cid] = new_rows.get(cid) or existing.get(cid)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for cid in sorted(merged, key=lambda x: int(x)):
            writer.writerow(merged[cid])


def run() -> str:
    if not os.getenv("GW2_API_KEY"):
        logger.error("GW2 API key not configured.")
        return "error"

    work = data_dir()
    local_csv = work / R2_WALLET_CSV_KEY

    client, bucket = s3_client()
    download_object_or_exit(client, bucket, R2_WALLET_CSV_KEY, local_csv)

    existing = load_existing(local_csv)

    wallet = fetch_wallet()
    if not wallet:
        return "no-data"

    metadata = fetch_currency_metadata([w["id"] for w in wallet])
    write_csv(local_csv, wallet, metadata, existing)

    return upload_file_if_changed(
        client,
        bucket,
        R2_WALLET_CSV_KEY,
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
        send_discord_alert(f"✅ {SCRIPT_LABEL} — No changes at <t:{ts}:f>")
    elif result == "no-data":
        send_discord_alert(f"{discord_user_prefix()}⚠️ {SCRIPT_LABEL} — No data at <t:{ts}:f>")
    else:
        send_discord_alert(f"{discord_user_prefix()}❌ {SCRIPT_LABEL} — Result {result} at <t:{ts}:f>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
