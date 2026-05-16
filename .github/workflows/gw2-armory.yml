"""Refresh the Legendary Armory ownership CSV with a metadata cache JSON."""

from __future__ import annotations

import csv
import json
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
    download_object_if_exists,
    download_object_or_exit,
    load_repo_env,
    env_required,
    s3_client,
    send_discord_alert,
    upload_file_if_changed,
)

R2_ARMORY_CSV_KEY = env_required("R2_ARMORY_CSV_KEY")
R2_ARMORY_CACHE_JSON_KEY = env_required("R2_ARMORY_CACHE_JSON_KEY")
SCRIPT_LABEL = "GW2 Legendary Armory"

ARMORY_URL = "https://api.guildwars2.com/v2/account/legendaryarmory"
ITEMS_URL = "https://api.guildwars2.com/v2/items"
FIELDNAMES = ["item_id", "name", "type", "subtype", "count", "icon", "weightclass"]

SKIP_ITEMS = {
    ("Consumable", "Unlock"),
    ("Container", "Default"),
    ("CraftingMaterial", "Unknown"),
    ("Gizmo", "Default"),
    ("Trophy", "Unknown"),
}

logger = logging.getLogger("gw2.armory")


def _headers() -> dict:
    key = os.getenv("GW2_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _is_relevant_meta(meta: dict) -> bool:
    return (meta.get("type", "Unknown"), meta.get("subtype", "Unknown")) not in SKIP_ITEMS


def _is_relevant_item(item: dict) -> bool:
    t = item.get("type", "Unknown")
    s = (item.get("details") or {}).get("type", "Unknown")
    return (t, s) not in SKIP_ITEMS


def fetch_armory() -> list[dict]:
    r = requests.get(ARMORY_URL, headers=_headers(), timeout=(5, 20))
    if not r.ok:
        logger.warning("Legendary armory returned status %s.", r.status_code)
        return []
    return r.json() or []


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def fetch_item_metadata(ids: list[int], cache: dict) -> dict:
    new_ids = [str(i) for i in ids if str(i) not in cache]
    if not new_ids:
        return cache
    logger.info("Fetching metadata for %d items.", len(new_ids))
    r = requests.get(f"{ITEMS_URL}?ids={','.join(new_ids)}", timeout=(5, 30))
    if not r.ok:
        logger.warning("Item metadata returned status %s.", r.status_code)
        return cache
    for item in r.json() or []:
        if not _is_relevant_item(item):
            continue
        cache[str(item["id"])] = {
            "name": item.get("name", "Unknown"),
            "type": item.get("type", "Unknown"),
            "subtype": (item.get("details") or {}).get("type", "Unknown"),
            "icon": item.get("icon", ""),
            "rarity": item.get("rarity", "Unknown"),
            "weightclass": (item.get("details") or {}).get("weight_class", ""),
        }
    return cache


def fetch_all_legendaries(cache: dict) -> dict:
    if cache.get("_all_legendaries_fetched"):
        return cache

    r = requests.get(ITEMS_URL, params={"rarity": "Legendary"}, timeout=(5, 30))
    if not r.ok:
        logger.warning("Legendary index returned status %s.", r.status_code)
        return cache
    all_ids = r.json() or []
    missing = [str(i) for i in all_ids if str(i) not in cache]
    logger.info("Backfilling metadata for %d legendaries.", len(missing))

    batch = 200
    for i in range(0, len(missing), batch):
        chunk = missing[i:i + batch]
        r2 = requests.get(f"{ITEMS_URL}?ids={','.join(chunk)}", timeout=(5, 30))
        if not r2.ok:
            continue
        for item in r2.json() or []:
            if not _is_relevant_item(item):
                continue
            cache[str(item["id"])] = {
                "name": item.get("name", "Unknown"),
                "type": item.get("type", "Unknown"),
                "subtype": (item.get("details") or {}).get("type", "Unknown"),
                "icon": item.get("icon", ""),
                "rarity": item.get("rarity", "Unknown"),
                "weightclass": (item.get("details") or {}).get("weight_class", ""),
            }

    cache["_all_legendaries_fetched"] = True
    return cache


def write_csv(path: Path, armory: list[dict], cache: dict) -> None:
    legendary_ids = [
        int(k) for k, meta in cache.items()
        if k != "_all_legendaries_fetched"
        and meta.get("rarity") == "Legendary"
        and _is_relevant_meta(meta)
    ]
    owned = {entry["id"]: entry.get("count", 0) for entry in armory}

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for iid in sorted(legendary_ids):
            meta = cache.get(str(iid), {})
            writer.writerow({
                "item_id": iid,
                "name": meta.get("name", "Unknown"),
                "type": meta.get("type", "Unknown"),
                "subtype": meta.get("subtype", "Unknown"),
                "count": owned.get(iid, 0),
                "icon": meta.get("icon", ""),
                "weightclass": meta.get("weightclass", ""),
            })


def run() -> str:
    if not os.getenv("GW2_API_KEY"):
        logger.error("GW2 API key not configured.")
        return "error"

    work = data_dir()
    local_csv = work / R2_ARMORY_CSV_KEY
    local_cache = work / R2_ARMORY_CACHE_JSON_KEY

    client, bucket = s3_client()
    download_object_or_exit(client, bucket, R2_ARMORY_CSV_KEY, local_csv)
    download_object_if_exists(client, bucket, R2_ARMORY_CACHE_JSON_KEY, local_cache)

    armory = fetch_armory()
    if not armory:
        return "no-data"

    cache = load_cache(local_cache)
    cache = fetch_item_metadata([a["id"] for a in armory], cache)
    cache = fetch_all_legendaries(cache)
    save_cache(local_cache, cache)

    write_csv(local_csv, armory, cache)

    csv_result = upload_file_if_changed(
        client, bucket, R2_ARMORY_CSV_KEY, local_csv,
        content_type="text/csv",
        cache_control="max-age=0, no-cache, must-revalidate",
    )
    cache_result = upload_file_if_changed(
        client, bucket, R2_ARMORY_CACHE_JSON_KEY, local_cache,
        content_type="application/json",
    )
    logger.info("Cache upload result: %s.", cache_result)
    return csv_result


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
