"""Refresh per-character age/last_modified CSV with a static profession cache."""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

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

R2_CHARACTERS_CSV_KEY = env_required("R2_CHARACTERS_CSV_KEY")
R2_CHARACTERS_STATIC_JSON_KEY = env_required("R2_CHARACTERS_STATIC_JSON_KEY")
SCRIPT_LABEL = "GW2 Char Details"

CHAR_LIST_URL = "https://api.guildwars2.com/v2/characters"
CHAR_DETAIL_URL = "https://api.guildwars2.com/v2/characters/{}"
FIELDNAMES = ["profession", "character_name", "age", "last_modified"]

logger = logging.getLogger("gw2.chars")


def _headers() -> dict:
    key = os.getenv("GW2_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _smart_delay(lo: float = 0.5, hi: float = 1.5) -> None:
    time.sleep(random.uniform(lo, hi))


def load_existing_csv(path: Path) -> dict:
    rows: dict = {}
    if not path.exists():
        return rows
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = row.get("character_name")
                if name:
                    rows[name] = row
    except OSError:
        logger.warning("Failed to read existing CSV.")
    return rows


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
        json.dump(cache, f, indent=2)


def build_static_cache(names: list[str], cache_path: Path) -> dict:
    cache = load_cache(cache_path)
    if cache and set(cache.keys()) == set(names):
        logger.info("Static cache already covers current roster.")
        return cache
    logger.info("Refreshing static profession cache.")

    static: dict = {}
    failed: list[str] = []

    def worker(name: str):
        try:
            with requests.Session() as s:
                r = s.get(
                    CHAR_DETAIL_URL.format(quote(name)),
                    headers=_headers(),
                    params={"v": "latest"},
                    timeout=(5, 20),
                )
                _smart_delay()
                if r.status_code == 200:
                    char = r.json()
                    return char.get("name"), char.get("profession", "Unknown")
        except requests.RequestException:
            return None
        return None

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(worker, n): n for n in names}
        for fut in as_completed(futures):
            name = futures[fut]
            res = fut.result()
            if res and res[0]:
                static[res[0]] = res[1]
            else:
                failed.append(name)

    for name in failed:
        res = worker(name)
        if res and res[0]:
            static[res[0]] = res[1]

    try:
        save_cache(cache_path, static)
    except OSError:
        logger.warning("Failed to write static cache.")
    return static


def fetch_detail(name: str, static: dict) -> dict | None:
    try:
        with requests.Session() as s:
            r = s.get(
                CHAR_DETAIL_URL.format(quote(name)),
                headers=_headers(),
                params={"v": "latest"},
                timeout=(5, 20),
            )
            _smart_delay()
            if r.status_code == 200:
                char = r.json()
                return {
                    "profession": static.get(char.get("name"), "Unknown"),
                    "character_name": char.get("name"),
                    "age": char.get("age", ""),
                    "last_modified": char.get("last_modified", ""),
                }
    except requests.RequestException:
        return None
    return None


def write_csv(path: Path, names: list[str], merged: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for n in names:
            if n in merged:
                writer.writerow(merged[n])


def run() -> str:
    if not os.getenv("GW2_API_KEY"):
        logger.error("GW2 API key not configured.")
        return "error"

    work = data_dir()
    local_csv = work / R2_CHARACTERS_CSV_KEY
    local_cache = work / R2_CHARACTERS_STATIC_JSON_KEY

    client, bucket = s3_client()
    download_object_or_exit(client, bucket, R2_CHARACTERS_CSV_KEY, local_csv)
    download_object_if_exists(client, bucket, R2_CHARACTERS_STATIC_JSON_KEY, local_cache)

    existing = load_existing_csv(local_csv)

    with requests.Session() as s:
        r = s.get(CHAR_LIST_URL, headers=_headers(), timeout=(5, 15))
    if not r.ok:
        logger.warning("Character list endpoint returned status %s.", r.status_code)
        return "error"
    names: list[str] = r.json() or []
    logger.info("Roster size: %d.", len(names))

    static = build_static_cache(names, local_cache)

    fetched: dict = {}
    success = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fetch_detail, n, static): n for n in names}
        for idx, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            if res:
                fetched[res["character_name"]] = res
                success += 1
            else:
                failed += 1
    logger.info("Detail fetch: %d ok, %d failed.", success, failed)

    merged = {n: fetched.get(n) or existing.get(n) for n in names if (fetched.get(n) or existing.get(n))}
    write_csv(local_csv, names, merged)

    csv_result = upload_file_if_changed(
        client, bucket, R2_CHARACTERS_CSV_KEY, local_csv,
        content_type="text/csv",
        cache_control="max-age=0, no-cache, must-revalidate",
    )
    cache_result = upload_file_if_changed(
        client, bucket, R2_CHARACTERS_STATIC_JSON_KEY, local_cache,
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
    else:
        send_discord_alert(f"{discord_user_prefix()}⚠️ {SCRIPT_LABEL} — Result {result} at <t:{ts}:f>")
        if result == "error":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
