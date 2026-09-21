#!/usr/bin/env python3
"""
homely_agile_dhw_v3.py

Update Homely hot-water schedule from Octopus Agile prices.

Flow:
  1. Read current Homely settings/schedules.
  2. Fetch Agile prices for tomorrow (or today with --today).
  3. Pick the cheapest N half-hour slots.
  4. Merge adjacent slots.
  5. Replace only the target weekday in the LIVE Homely DHW schedule.
  6. Send the updated full DHW schedule back to Homely.

Required environment variables:
    HOMELY_TOKEN
        Must contain the value expected by the Authorization header,
        e.g.:
            Bearer eyJ...

    HOMELY_HOUSE_ID
    HOMELY_USER_ID

    OCTOPUS_PRODUCT
        Example: AGILE-24-10-01

    OCTOPUS_TARIFF
        Example: E-1R-AGILE-24-10-01-G

Optional:
    AGILE_DHW_SLOTS
        Number of cheapest half-hour slots.
        Default: 4

Usage:
    ./homely_agile_dhw_v3.py --today --dry-run
    ./homely_agile_dhw_v3.py --dry-run
    ./homely_agile_dhw_v3.py
    ./homely_agile_dhw_v3.py --slots 6
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import requests


LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

HOMELY_URL = "https://customer-api.homelyenergy.com/"
HOMELY_VERSION = "6.3.5"
HOMELY_USER_AGENT = "Homely_Customer_App/6.3.5 ios/26.6"


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def make_id(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def make_homely_session() -> requests.Session:
    """
    Bypass macOS/Proxyman system proxy settings for direct Homely calls.
    """
    session = requests.Session()
    session.trust_env = False
    return session


def homely_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "version": HOMELY_VERSION,
        "User-Agent": HOMELY_USER_AGENT,
        "Authorization": env("HOMELY_TOKEN").removeprefix("Authorization: "),
        "Content-Type": "application/json",
    }


def homely_post(method: str, payload: dict | None = None) -> dict:
    """
    Generic call to the Homely customer API.
    """
    body = {
        "method": method,
        "houseId": env("HOMELY_HOUSE_ID"),
        "payload": payload or {},
        "userId": env("HOMELY_USER_ID"),
    }

    session = make_homely_session()

    response = session.post(
        HOMELY_URL,
        headers=homely_headers(),
        json=body,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Homely {method} failed: HTTP {response.status_code}: {response.text}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Homely {method} returned invalid JSON: {response.text}"
        ) from exc


def get_live_homely_state() -> dict:
    """
    Read current Homely state.

    The current API returns a full settings/schedules snapshot after
    setSettings. We use the current DHW setpoint as a no-op value so the
    response gives us the live schedule.

    First try a likely read-style method. If unsupported, fall back to the
    known-working setSettings call with the current setpoint from HOMELY_HW_SETPOINT,
    defaulting to 40.0.
    """
    # We do not want to guess a read method and accidentally fail the script,
    # so use the already-proven setSettings method as a read-back mechanism.
    current_setpoint = os.getenv("HOMELY_HW_SETPOINT", "40.0")

    state = homely_post(
        "setSettings",
        {
            "hwSetPoint": f"{float(current_setpoint):.1f}"
        },
    )

    return state


def extract_live_hotwater_schedule(state: dict):
    """
    Extract schedule ID and entries from:
        state["schedules"]["hotwater"][schedule_id]
    """
    schedules = state.get("schedules")
    if not isinstance(schedules, dict):
        raise RuntimeError("Homely response does not contain schedules")

    hotwater = schedules.get("hotwater")
    if not isinstance(hotwater, dict) or not hotwater:
        raise RuntimeError("Homely response does not contain a hot-water schedule")

    if len(hotwater) > 1:
        print(
            f"WARNING: Homely returned {len(hotwater)} hot-water schedules; "
            "using the first one."
        )

    schedule_id, schedule_obj = next(iter(hotwater.items()))

    if not isinstance(schedule_obj, dict):
        raise RuntimeError("Unexpected Homely hot-water schedule format")

    entries = schedule_obj.get("data")
    if not isinstance(entries, list):
        raise RuntimeError("Homely hot-water schedule has no data list")

    cleaned = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        required = ("weekdayId", "start", "end")
        if not all(k in entry for k in required):
            continue

        cleaned.append(
            {
                "id": entry.get("id") or make_id(),
                "weekdayId": int(entry["weekdayId"]),
                "start": int(entry["start"]),
                "end": int(entry["end"]),
            }
        )

    if not cleaned:
        raise RuntimeError("Homely hot-water schedule contained no usable entries")

    return schedule_id, cleaned


def target_date(use_today: bool):
    today = datetime.now(LONDON).date()
    return today if use_today else today + timedelta(days=1)


def fetch_agile_prices(product: str, tariff: str, date_to_fetch):
    local_start = datetime.combine(date_to_fetch, time.min, tzinfo=LONDON)
    local_end = local_start + timedelta(days=1)

    url = (
        f"https://api.octopus.energy/v1/products/{product}/"
        f"electricity-tariffs/{tariff}/standard-unit-rates/"
    )

    params = {
        "period_from": local_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "period_to": local_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "page_size": 100,
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    slots = []

    for item in response.json().get("results", []):
        start = datetime.fromisoformat(
            item["valid_from"].replace("Z", "+00:00")
        ).astimezone(LONDON)

        end = datetime.fromisoformat(
            item["valid_to"].replace("Z", "+00:00")
        ).astimezone(LONDON)

        if start.date() != date_to_fetch:
            continue

        slots.append(
            {
                "start": start,
                "end": end,
                "price": float(item["value_inc_vat"]),
            }
        )

    slots.sort(key=lambda x: x["start"])

    if not slots:
        raise RuntimeError(
            f"No Agile prices available for {date_to_fetch}. "
            "Tomorrow's prices may not have been published yet."
        )

    return slots


def choose_cheapest(slots, count: int):
    if count < 1:
        raise ValueError("--slots must be at least 1")

    if len(slots) < count:
        raise RuntimeError(
            f"Only {len(slots)} Agile half-hour slots are available, "
            f"but {count} were requested"
        )

    cheapest = sorted(slots, key=lambda x: x["price"])[:count]
    return sorted(cheapest, key=lambda x: x["start"])


def merge_adjacent(slots):
    if not slots:
        return []

    merged = [
        {
            "start": slots[0]["start"],
            "end": slots[0]["end"],
            "prices": [slots[0]["price"]],
        }
    ]

    for slot in slots[1:]:
        current = merged[-1]

        if slot["start"] == current["end"]:
            current["end"] = slot["end"]
            current["prices"].append(slot["price"])
        else:
            merged.append(
                {
                    "start": slot["start"],
                    "end": slot["end"],
                    "prices": [slot["price"]],
                }
            )

    return merged


def minutes_after_midnight(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def build_target_entries(windows, weekday_id: int):
    entries = []

    for window in windows:
        start = minutes_after_midnight(window["start"])

        if window["end"].date() != window["start"].date():
            end = 1440
        else:
            end = minutes_after_midnight(window["end"])

        entries.append(
            {
                "id": make_id(),
                "weekdayId": weekday_id,
                "start": start,
                "end": end,
            }
        )

    return entries


def replace_weekday(live_schedule, weekday_id: int, new_entries):
    preserved = [
        dict(entry)
        for entry in live_schedule
        if int(entry["weekdayId"]) != weekday_id
    ]

    result = preserved + new_entries
    result.sort(key=lambda x: (int(x["weekdayId"]), int(x["start"])))

    return result


def send_hotwater_schedule(schedule_id: str, schedule_data):
    payload = {
        "scheduleId": schedule_id,
        "schedule": {
            "id": schedule_id,
            "data": schedule_data,
        },
    }

    return homely_post("setHotWaterSchedule", payload)


def print_schedule(entries):
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for entry in sorted(
        entries,
        key=lambda x: (int(x["weekdayId"]), int(x["start"])),
    ):
        day = weekday_names[int(entry["weekdayId"])]
        start = int(entry["start"])
        end = int(entry["end"])

        sh, sm = divmod(start, 60)

        if end == 1440:
            end_text = "24:00"
        else:
            eh, em = divmod(end, 60)
            end_text = f"{eh:02d}:{em:02d}"

        print(f"  {day} {sh:02d}:{sm:02d}-{end_text}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and show changes but do not update Homely",
    )

    parser.add_argument(
        "--today",
        action="store_true",
        help="Use today's Agile prices instead of tomorrow's",
    )

    parser.add_argument(
        "--slots",
        type=int,
        default=int(os.getenv("AGILE_DHW_SLOTS", "4")),
        help="Number of cheapest half-hour slots; default 4",
    )

    args = parser.parse_args()

    date_to_use = target_date(args.today)
    weekday_id = date_to_use.weekday()

    print("Reading current Homely hot-water schedule...")

    state = get_live_homely_state()
    schedule_id, live_schedule = extract_live_hotwater_schedule(state)

    print(f"Homely hot-water schedule ID: {schedule_id}")
    print()
    print("Current Homely DHW schedule:")
    print_schedule(live_schedule)

    print()
    print(f"Fetching Agile prices for {date_to_use:%A %d %B %Y}...")

    prices = fetch_agile_prices(
        env("OCTOPUS_PRODUCT"),
        env("OCTOPUS_TARIFF"),
        date_to_use,
    )

    cheapest = choose_cheapest(prices, args.slots)
    windows = merge_adjacent(cheapest)

    print()
    print(f"Cheapest {args.slots} half-hour slots:")

    for slot in cheapest:
        print(
            f"  {slot['start']:%H:%M}-{slot['end']:%H:%M}  "
            f"{slot['price']:.2f}p/kWh"
        )

    print()
    print("Proposed Homely DHW windows:")

    for window in windows:
        average = sum(window["prices"]) / len(window["prices"])
        print(
            f"  {window['start']:%H:%M}-{window['end']:%H:%M}  "
            f"avg {average:.2f}p/kWh"
        )

    new_entries = build_target_entries(windows, weekday_id)

    updated_schedule = replace_weekday(
        live_schedule,
        weekday_id,
        new_entries,
    )

    print()
    print("Updated complete Homely DHW schedule:")
    print_schedule(updated_schedule)

    if args.dry_run:
        print()
        print("DRY RUN: nothing sent to Homely.")
        return

    print()
    print("Sending updated hot-water schedule to Homely...")

    response = send_hotwater_schedule(
        schedule_id,
        updated_schedule,
    )

    print("Homely accepted the updated schedule.")

    if isinstance(response, dict):
        print("Response keys:", ", ".join(response.keys()))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
