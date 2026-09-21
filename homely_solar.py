#!/usr/bin/env python3
"""
homely.py

Multi-profile Homely + Octopus Agile DHW scheduler.

All configuration is read from homely.env.
Host environment variables are NOT used.

Profile selection:
    ./homely.py --profile Richard --auth-test
    ./homely.py --profile Richard --today --dry-run
    ./homely.py --profile Richard
    ./homely.py --all-profiles --dry-run
    ./homely.py --all-profiles

If --profile is omitted, DEFAULT_PROFILE from homely.env is used.

Required profile settings:
    <PROFILE>.HOMELY_EMAIL
    <PROFILE>.HOMELY_PASSWORD
    <PROFILE>.OCTOPUS_PRODUCT
    <PROFILE>.OCTOPUS_TARIFF

Email reporting settings (shared or profile-specific):
    SMTP_SERVER
    SMTP_PORT
    EMAIL_FROM
    EMAIL_PASSWORD
    <PROFILE>.EMAIL_TO

If <PROFILE>.EMAIL_TO is omitted, the profile's HOMELY_EMAIL is used.

Optional profile settings:
    <PROFILE>.AGILE_DHW_SLOTS
    <PROFILE>.LAT
    <PROFILE>.LON
    <PROFILE>.SOLAR_ARRAY_KWP
    <PROFILE>.SOLAR_PERFORMANCE
    <PROFILE>.DHW_LOAD_KW

Shared values for the same settings may also be used.

Solar-adjusted slot ranking uses the same physical model as energy.py:
    solar_factor       = shortwave_radiation_Wm2 / 1000
    expected_solar_kw  = SOLAR_ARRAY_KWP * solar_factor * SOLAR_PERFORMANCE
    expected_solar_kwh = expected_solar_kw * 0.5
    dhw_load_kwh       = DHW_LOAD_KW * 0.5
    grid_needed_kwh    = max(dhw_load_kwh - expected_solar_kwh, 0)
    expected_cost_p    = grid_needed_kwh * Agile_price
    adjusted_price     = expected_cost_p / dhw_load_kwh
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import smtplib
import secrets
import string
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values


AUTH_URL = "https://oauth.homelyenergy.com/auth"
TOKEN_URL = "https://oauth.homelyenergy.com/token"
CUSTOMER_API_URL = "https://customer-api.homelyenergy.com/"

CLIENT_ID = "kh23jh"
SCOPE = "mobile"
REDIRECT_URI = "auth.homelyenergy://oauthredirect"

APP_VERSION = "6.3.5"
CUSTOMER_UA = "Homely_Customer_App/6.3.5 ios/26.6"
OAUTH_UA = "Homely/190 CFNetwork/3860.700.1 Darwin/25.6.0"


class Config:
    def __init__(self, env_file: Path, profile: str | None):
        if not env_file.exists():
            raise RuntimeError(f"Config file not found: {env_file}")

        raw = dotenv_values(env_file)

        # dotenv_values may return None values for malformed/bare keys.
        self.values = {
            str(k): str(v)
            for k, v in raw.items()
            if k is not None and v is not None
        }

        selected = profile or self.values.get("DEFAULT_PROFILE")
        if not selected:
            raise RuntimeError(
                "No profile selected. Use --profile NAME or set "
                "DEFAULT_PROFILE in homely_solar.env"
            )

        self.profile = selected

    def shared(self, key: str, default: str | None = None) -> str:
        value = self.values.get(key)
        if value is None or value == "":
            if default is not None:
                return default
            raise RuntimeError(f"Required config setting {key} is missing")
        return value

    def available_profiles(self) -> list[str]:
        """Discover profiles from keys of the form PROFILE.HOMELY_EMAIL."""
        suffix = ".HOMELY_EMAIL"
        profiles = sorted(
            key[:-len(suffix)]
            for key in self.values
            if key.endswith(suffix) and key[:-len(suffix)]
        )
        if not profiles:
            raise RuntimeError(
                "No profiles found in config. Expected keys such as "
                "Richard.HOMELY_EMAIL"
            )
        return profiles

    def get(self, key: str, default: str | None = None) -> str:
        """
        Read PROFILE.KEY first, then shared KEY, then default.
        """
        profile_key = f"{self.profile}.{key}"

        value = self.values.get(profile_key)
        if value is not None and value != "":
            return value

        value = self.values.get(key)
        if value is not None and value != "":
            return value

        if default is not None:
            return default

        raise RuntimeError(
            f"Required config setting {profile_key} is missing "
            f"(and no shared {key} is set)"
        )


def direct_session() -> requests.Session:
    # Do not inherit proxy or other host-environment settings.
    session = requests.Session()
    session.trust_env = False
    return session


def base64url_no_padding(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64url_no_padding(
        hashlib.sha256(verifier.encode("ascii")).digest()
    )
    return verifier, challenge


def homely_login(config: Config) -> dict:
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    auth_body = {
        "email": config.get("HOMELY_EMAIL"),
        "password": config.get("HOMELY_PASSWORD"),
        "params": {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "scope": SCOPE,
            "state": state,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    }

    session = direct_session()

    auth_response = session.post(
        AUTH_URL,
        headers={
            "Accept": "*/*",
            "User-Agent": OAUTH_UA,
            "Content-Type": "application/json",
        },
        json=auth_body,
        timeout=30,
    )

    if not auth_response.ok:
        raise RuntimeError(
            f"Homely OAuth /auth failed: HTTP {auth_response.status_code}: "
            f"{auth_response.text}"
        )

    auth_data = auth_response.json()

    if auth_data.get("state") != state:
        raise RuntimeError("Homely OAuth state mismatch")

    code = auth_data.get("code")
    if not code:
        raise RuntimeError("Homely OAuth /auth returned no authorization code")

    token_response = session.post(
        TOKEN_URL,
        headers={
            "Accept": "*/*",
            "User-Agent": OAUTH_UA,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )

    if not token_response.ok:
        raise RuntimeError(
            f"Homely OAuth /token failed: HTTP {token_response.status_code}: "
            f"{token_response.text}"
        )

    token_data = token_response.json()

    if not token_data.get("access_token"):
        raise RuntimeError("Homely OAuth /token returned no access_token")

    return token_data


class HomelyClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.session = direct_session()
        self.user_id: str | None = None
        self.house_id: str | None = None
        self.settings: dict | None = None
        self.timezone_name: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "version": APP_VERSION,
            "User-Agent": CUSTOMER_UA,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def post(self, body: dict) -> dict:
        response = self.session.post(
            CUSTOMER_API_URL,
            headers=self.headers,
            json=body,
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"Homely customer API failed: HTTP {response.status_code}: "
                f"{response.text}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Homely customer API returned invalid JSON: {response.text}"
            ) from exc

    def discover_identity(self) -> tuple[str, str]:
        info = self.post({"method": "userInfo"})
        user_id = info.get("userId")

        if not user_id:
            raise RuntimeError("Homely userInfo response contained no userId")

        user = self.post(
            {
                "method": "getUser",
                "payload": {"userId": user_id},
                "userId": user_id,
            }
        )

        house_ids = user.get("houseIds")
        if not isinstance(house_ids, list) or not house_ids:
            raise RuntimeError("Homely getUser response contained no houseIds")

        if len(house_ids) > 1:
            raise RuntimeError(
                f"Profile {user_id[:6]}... has {len(house_ids)} houses. "
                "The script will not guess which one to control."
            )

        self.user_id = user_id
        self.house_id = house_ids[0]
        return self.user_id, self.house_id

    def require_identity(self) -> None:
        if not self.user_id or not self.house_id:
            raise RuntimeError("Homely identity has not been discovered")

    def get_settings(self) -> dict:
        self.require_identity()

        response = self.post(
            {
                "method": "getSettings",
                "houseId": self.house_id,
                "userId": self.user_id,
            }
        )

        self.settings = response

        timezone_name = response.get("timezone")
        if not timezone_name:
            raise RuntimeError("Homely getSettings response contained no timezone")

        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise RuntimeError(
                f"Homely returned unsupported timezone: {timezone_name}"
            ) from exc

        self.timezone_name = timezone_name
        return response

    def get_all_schedules(self) -> dict:
        self.require_identity()

        return self.post(
            {
                "method": "getAllSchedules",
                "houseId": self.house_id,
                "userId": self.user_id,
            }
        )

    def set_hotwater_schedule(self, schedule_id: str, data: list[dict]) -> dict:
        self.require_identity()

        return self.post(
            {
                "method": "setHotWaterSchedule",
                "houseId": self.house_id,
                "payload": {
                    "scheduleId": schedule_id,
                    "schedule": {
                        "id": schedule_id,
                        "data": data,
                    },
                },
                "userId": self.user_id,
            }
        )


def make_entry_id(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def extract_hotwater_schedule(schedules_response: dict) -> tuple[str, list[dict]]:
    schedules = schedules_response.get("schedules")
    if not isinstance(schedules, dict):
        raise RuntimeError("getAllSchedules response contained no schedules object")

    hotwater = schedules.get("hotwater")
    if not isinstance(hotwater, list) or not hotwater:
        raise RuntimeError("getAllSchedules response contained no hot-water schedule")

    if len(hotwater) > 1:
        raise RuntimeError(
            f"Homely returned {len(hotwater)} hot-water schedules. "
            "The script will not guess which one to update."
        )

    schedule = hotwater[0]
    schedule_id = schedule.get("id")
    entries = schedule.get("data")

    if not schedule_id:
        raise RuntimeError("Homely hot-water schedule contained no id")

    if not isinstance(entries, list):
        raise RuntimeError("Homely hot-water schedule contained no data list")

    cleaned = []

    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Unexpected non-object entry in hot-water schedule")

        for required in ("weekdayId", "start", "end"):
            if required not in entry:
                raise RuntimeError(
                    f"Hot-water schedule entry is missing {required}: {entry}"
                )

        cleaned.append(
            {
                "id": entry.get("id") or make_entry_id(),
                "weekdayId": int(entry["weekdayId"]),
                "start": int(entry["start"]),
                "end": int(entry["end"]),
            }
        )

    return schedule_id, cleaned


def validate_dhw_settings(settings: dict) -> None:
    if settings.get("hasWater") is not True:
        raise RuntimeError("Homely reports that this installation has no DHW control")

    setpoint_range = settings.get("dhwSetpointRange")
    if not isinstance(setpoint_range, dict):
        raise RuntimeError("Homely getSettings returned no DHW setpoint range")

    if "min" not in setpoint_range or "max" not in setpoint_range:
        raise RuntimeError("Homely DHW setpoint range is incomplete")


def requested_date(timezone: ZoneInfo, use_today: bool):
    today = datetime.now(timezone).date()
    return today if use_today else today + timedelta(days=1)


def fetch_agile_prices(
    product: str,
    tariff: str,
    target_date,
    timezone: ZoneInfo,
) -> list[dict]:
    utc = ZoneInfo("UTC")

    local_start = datetime.combine(target_date, time.min, tzinfo=timezone)
    local_end = local_start + timedelta(days=1)

    url = (
        f"https://api.octopus.energy/v1/products/{product}/"
        f"electricity-tariffs/{tariff}/standard-unit-rates/"
    )

    params = {
        "period_from": local_start.astimezone(utc).isoformat().replace("+00:00", "Z"),
        "period_to": local_end.astimezone(utc).isoformat().replace("+00:00", "Z"),
        "page_size": 100,
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    slots = []

    for item in response.json().get("results", []):
        start = datetime.fromisoformat(
            item["valid_from"].replace("Z", "+00:00")
        ).astimezone(timezone)

        end = datetime.fromisoformat(
            item["valid_to"].replace("Z", "+00:00")
        ).astimezone(timezone)

        if start.date() != target_date:
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
            f"No Agile prices available for {target_date}. "
            "Tomorrow's prices may not have been published yet."
        )

    return slots


def fetch_solar_forecast(latitude: float, longitude: float, target_date, timezone: ZoneInfo) -> list[dict]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "shortwave_radiation",
        "forecast_days": 3,
        "timezone": "UTC",
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    radiation = hourly.get("shortwave_radiation", [])
    if not times or not radiation or len(times) != len(radiation):
        raise RuntimeError("Open-Meteo returned incomplete solar-radiation data")
    points = []
    for ts, rad in zip(times, radiation):
        dt_utc = datetime.fromisoformat(ts).replace(tzinfo=ZoneInfo("UTC"))
        points.append((dt_utc, float(rad or 0.0)))
    points.sort(key=lambda item: item[0])
    local_start = datetime.combine(target_date, time.min, tzinfo=timezone)
    local_end = local_start + timedelta(days=1)
    slots = []
    current = local_start
    while current < local_end:
        current_utc = current.astimezone(ZoneInfo("UTC"))
        before = None
        after = None
        for point in points:
            if point[0] <= current_utc:
                before = point
            if point[0] >= current_utc:
                after = point
                break
        if before is None and after is None:
            rad = 0.0
        elif before is None:
            rad = after[1]
        elif after is None:
            rad = before[1]
        elif after[0] == before[0]:
            rad = before[1]
        else:
            span = (after[0] - before[0]).total_seconds()
            elapsed = (current_utc - before[0]).total_seconds()
            fraction = elapsed / span
            rad = before[1] + (after[1] - before[1]) * fraction
        slots.append({"start": current, "radiation": max(0.0, float(rad))})
        current += timedelta(minutes=30)
    return slots


def add_solar_weighting(agile_slots: list[dict], solar_slots: list[dict], solar_array_kwp: float, solar_performance: float, dhw_load_kw: float) -> list[dict]:
    if solar_array_kwp <= 0:
        raise RuntimeError("SOLAR_ARRAY_KWP must be greater than zero")
    if solar_performance <= 0:
        raise RuntimeError("SOLAR_PERFORMANCE must be greater than zero")
    if dhw_load_kw <= 0:
        raise RuntimeError("DHW_LOAD_KW must be greater than zero")
    solar_by_start = {item["start"]: float(item["radiation"]) for item in solar_slots}
    slot_hours = 0.5
    load_kwh = dhw_load_kw * slot_hours
    weighted = []
    for slot in agile_slots:
        item = dict(slot)
        radiation = solar_by_start.get(item["start"], 0.0)
        solar_factor = max(0.0, min(radiation / 1000.0, 1.0))
        expected_solar_kw = solar_array_kwp * solar_factor * solar_performance
        expected_solar_kwh = expected_solar_kw * slot_hours
        grid_needed_kwh = max(load_kwh - expected_solar_kwh, 0.0)
        expected_cost_p = grid_needed_kwh * item["price"]
        adjusted_price = expected_cost_p / load_kwh
        item.update({
            "radiation": radiation,
            "solar_factor": solar_factor,
            "expected_solar_kw": expected_solar_kw,
            "expected_solar_kwh": expected_solar_kwh,
            "dhw_load_kwh": load_kwh,
            "grid_needed_kwh": grid_needed_kwh,
            "expected_cost_p": expected_cost_p,
            "adjusted_price": adjusted_price,
        })
        weighted.append(item)
    return weighted


def choose_cheapest(slots: list[dict], count: int) -> list[dict]:
    if count < 1:
        raise ValueError("--slots must be at least 1")

    if len(slots) < count:
        raise RuntimeError(
            f"Only {len(slots)} Agile slots are available, but {count} were requested"
        )

    ranking_key = "adjusted_price" if slots and "adjusted_price" in slots[0] else "price"
    cheapest = sorted(slots, key=lambda x: x[ranking_key])[:count]
    return sorted(cheapest, key=lambda x: x["start"])


def merge_adjacent(slots: list[dict]) -> list[dict]:
    if not slots:
        return []

    merged = []

    for slot in slots:
        if merged and slot["start"] == merged[-1]["end"]:
            merged[-1]["end"] = slot["end"]
            merged[-1]["prices"].append(slot["price"])
            merged[-1]["radiations"].append(slot.get("radiation", 0.0))
            merged[-1]["adjusted_prices"].append(slot.get("adjusted_price", slot["price"]))
        else:
            merged.append(
                {
                    "start": slot["start"],
                    "end": slot["end"],
                    "prices": [slot["price"]],
                    "radiations": [slot.get("radiation", 0.0)],
                    "adjusted_prices": [slot.get("adjusted_price", slot["price"])],
                }
            )

    return merged


def minutes_after_midnight(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def build_entries(windows: list[dict], weekday_id: int) -> list[dict]:
    result = []

    for window in windows:
        start = minutes_after_midnight(window["start"])

        if window["end"].date() != window["start"].date():
            end = 1440
        else:
            end = minutes_after_midnight(window["end"])

        result.append(
            {
                "id": make_entry_id(),
                "weekdayId": weekday_id,
                "start": start,
                "end": end,
            }
        )

    return result


def replace_weekday(
    live_schedule: list[dict],
    weekday_id: int,
    new_entries: list[dict],
) -> list[dict]:
    preserved = [
        dict(entry)
        for entry in live_schedule
        if int(entry["weekdayId"]) != weekday_id
    ]

    result = preserved + new_entries
    result.sort(key=lambda x: (int(x["weekdayId"]), int(x["start"])))

    return result


def print_schedule(entries: list[dict]) -> None:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for entry in sorted(
        entries,
        key=lambda x: (int(x["weekdayId"]), int(x["start"])),
    ):
        weekday_id = int(entry["weekdayId"])

        if weekday_id < 0 or weekday_id > 6:
            raise RuntimeError(f"Unexpected Homely weekdayId: {weekday_id}")

        start = int(entry["start"])
        end = int(entry["end"])

        sh, sm = divmod(start, 60)

        if end == 1440:
            end_text = "24:00"
        else:
            eh, em = divmod(end, 60)
            end_text = f"{eh:02d}:{em:02d}"

        print(f"  {names[weekday_id]} {sh:02d}:{sm:02d}-{end_text}")



class Tee:
    """Write stdout to both the terminal and an in-memory report buffer."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def email_recipients(config: Config) -> list[str]:
    """
    Read PROFILE.EMAIL_TO first, then shared EMAIL_TO.
    If neither exists, use the profile's HOMELY_EMAIL.
    Comma- or semicolon-separated recipients are supported.
    """
    recipient_text = config.get(
        "EMAIL_TO",
        config.get("HOMELY_EMAIL"),
    )

    recipients = [
        item.strip()
        for item in recipient_text.replace(";", ",").split(",")
        if item.strip()
    ]

    if not recipients:
        raise RuntimeError(f"No email recipient configured for {config.profile}")

    return recipients


def send_activity_email(
    config: Config,
    report_text: str,
    status: str,
) -> None:
    """Email one profile's complete activity report."""

    smtp_server = config.get("SMTP_SERVER")
    smtp_port = int(config.get("SMTP_PORT"))
    email_from = config.get("EMAIL_FROM")
    email_password = config.get("EMAIL_PASSWORD")
    email_from_name = config.get("EMAIL_FROM_NAME", "Homely Agile")
    recipients = email_recipients(config)

    now = datetime.now().astimezone()
    subject = (
        f"Homely DHW - {config.profile} - {status} - "
        f"{now:%d %b %Y %H:%M}"
    )

    escaped_report = html.escape(report_text)

    html_body = f"""\
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    color: #222;
    line-height: 1.45;
}}
h2 {{ margin-bottom: 4px; }}
.status {{ font-weight: 600; }}
pre {{
    white-space: pre-wrap;
    word-wrap: break-word;
    background: #f5f5f5;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 12px;
    font-family: Menlo, Monaco, Consolas, monospace;
    font-size: 13px;
}}
.small {{ color: #666; font-size: 12px; }}
</style>
</head>
<body>
<h2>Homely hot-water scheduler</h2>
<p><b>Profile:</b> {html.escape(config.profile)}<br>
<b>Status:</b> <span class="status">{html.escape(status)}</span><br>
<b>Run time:</b> {now:%A %d %B %Y %H:%M %Z}</p>

<pre>{escaped_report}</pre>

<p class="small">
Generated automatically by homely.py.
</p>
</body>
</html>
"""

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((email_from_name, email_from))
    msg["To"] = ", ".join(recipients)

    msg.set_content(report_text)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL(
        smtp_server,
        smtp_port,
        timeout=30,
    ) as smtp:
        smtp.login(
            email_from,
            email_password.replace(" ", ""),
        )
        smtp.send_message(msg)




def _run_profile_core(env_file: Path, profile_name: str, args) -> None:
    config = Config(env_file, profile_name)

    slots = (
        args.slots
        if args.slots is not None
        else int(config.get("AGILE_DHW_SLOTS", "4"))
    )

    latitude = float(config.get("LAT"))
    longitude = float(config.get("LON"))
    solar_array_kwp = float(config.get("SOLAR_ARRAY_KWP"))
    solar_performance = float(config.get("SOLAR_PERFORMANCE"))
    dhw_load_kw = float(config.get("DHW_LOAD_KW"))

    print(f"Profile: {config.profile}")
    print(f"Config:  {env_file}")

    print("Logging into Homely using OAuth PKCE...")
    token_data = homely_login(config)

    access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in")

    print(f"Homely login successful; access token lifetime: {expires_in} seconds")

    client = HomelyClient(access_token)

    print("Discovering Homely user and house...")
    user_id, house_id = client.discover_identity()

    print(f"User ID discovered:  {user_id[:6]}...{user_id[-4:]}")
    print(f"House ID discovered: {house_id[:6]}...{house_id[-4:]}")

    print("Reading Homely settings...")
    settings = client.get_settings()
    validate_dhw_settings(settings)

    timezone_name = client.timezone_name
    timezone = ZoneInfo(timezone_name)

    print(f"Homely timezone: {timezone_name}")
    print(f"Current DHW setpoint: {settings.get('hwSetPoint')}°C")

    dhw_range = settings["dhwSetpointRange"]
    print(f"DHW setpoint range: {dhw_range['min']}–{dhw_range['max']}°C")

    print("Reading live Homely schedules...")
    schedules_response = client.get_all_schedules()
    schedule_id, live_schedule = extract_hotwater_schedule(schedules_response)

    print(f"Hot-water schedule ID discovered: {schedule_id}")

    if args.auth_test:
        print("Authentication and dynamic discovery test successful.")
        return

    print()
    print("Current Homely DHW schedule:")
    print_schedule(live_schedule)

    target_date = requested_date(timezone, args.today)
    weekday_id = target_date.weekday()

    print()
    print(f"Fetching Agile prices for {target_date:%A %d %B %Y}...")

    prices = fetch_agile_prices(
        config.get("OCTOPUS_PRODUCT"),
        config.get("OCTOPUS_TARIFF"),
        target_date,
        timezone,
    )

    print(f"Fetching solar forecast for {latitude:.5f}, {longitude:.5f}...")
    solar = fetch_solar_forecast(latitude, longitude, target_date, timezone)
    weighted_prices = add_solar_weighting(
        prices, solar, solar_array_kwp, solar_performance, dhw_load_kw
    )

    cheapest = choose_cheapest(weighted_prices, slots)
    windows = merge_adjacent(cheapest)

    print()
    print(
        "Solar weighting: "
        f"{solar_array_kwp:.2f} kWp PV, "
        f"{solar_performance:.0%} performance, "
        f"{dhw_load_kw:.2f} kW DHW electrical load"
    )

    print()
    print(f"Best {slots} solar-adjusted half-hour slots:")
    for slot in cheapest:
        print(
            f"  {slot['start']:%H:%M}-{slot['end']:%H:%M}  "
            f"Agile {slot['price']:.2f}p/kWh  "
            f"Radiation {slot['radiation']:.0f} W/m²  "
            f"Solar {slot['expected_solar_kwh']:.2f} kWh  "
            f"Grid {slot['grid_needed_kwh']:.2f} kWh  "
            f"Adjusted {slot['adjusted_price']:.2f}p/kWh"
        )

    print()
    print("Proposed Homely DHW windows:")
    for window in windows:
        average_price = sum(window["prices"]) / len(window["prices"])
        average_radiation = sum(window["radiations"]) / len(window["radiations"])
        average_adjusted = sum(window["adjusted_prices"]) / len(window["adjusted_prices"])
        print(
            f"  {window['start']:%H:%M}-{window['end']:%H:%M}  "
            f"Agile avg {average_price:.2f}p/kWh  "
            f"Radiation avg {average_radiation:.0f} W/m²  "
            f"Adjusted avg {average_adjusted:.2f}p/kWh"
        )

    new_entries = build_entries(windows, weekday_id)

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

    client.set_hotwater_schedule(
        schedule_id,
        updated_schedule,
    )

    print("Homely accepted the updated schedule.")


def run_profile(env_file: Path, profile_name: str, args) -> None:
    """
    Run one profile, echo activity to the terminal, capture it for email,
    and send an email for success or failure.
    """
    config = Config(env_file, profile_name)
    report_buffer = io.StringIO()
    tee = Tee(sys.stdout, report_buffer)

    try:
        with redirect_stdout(tee):
            _run_profile_core(env_file, profile_name, args)

        if args.auth_test:
            status = "AUTH TEST OK"
        elif args.dry_run:
            status = "DRY RUN"
        else:
            status = "SCHEDULE UPDATED"

        report_text = report_buffer.getvalue()
        send_activity_email(config, report_text, status)

        print(
            f"Activity email sent for {profile_name} to "
            f"{', '.join(email_recipients(config))}"
        )

    except Exception as exc:
        report_text = report_buffer.getvalue()

        if report_text and not report_text.endswith("\n"):
            report_text += "\n"

        report_text += f"\nERROR: {exc}\n"

        # Try to send the failure report, but never hide the original error
        # if the email itself also fails.
        try:
            send_activity_email(config, report_text, "FAILED")
            print(
                f"Failure email sent for {profile_name} to "
                f"{', '.join(email_recipients(config))}"
            )
        except Exception as email_exc:
            print(
                f"WARNING: Could not send failure email for {profile_name}: "
                f"{email_exc}",
                file=sys.stderr,
            )

        raise


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--profile",
        help="Profile name from homely.env; defaults to DEFAULT_PROFILE",
    )

    parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="Run every profile found in homely.env",
    )

    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).with_name("homely_solar.env")),
        help="Config file path; default: homely_solar.env beside this script",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed change but do not write the DHW schedule",
    )

    parser.add_argument(
        "--today",
        action="store_true",
        help="Use today's Agile prices rather than tomorrow's",
    )

    parser.add_argument(
        "--auth-test",
        action="store_true",
        help="Test Homely login and dynamic discovery, then exit",
    )

    parser.add_argument(
        "--slots",
        type=int,
        help="Override number of cheapest half-hour slots",
    )

    args = parser.parse_args()

    if args.all_profiles and args.profile:
        parser.error("--all-profiles cannot be used together with --profile")

    env_file = Path(args.env_file).expanduser()

    # Load once to discover profiles/default profile.
    base_config = Config(env_file, args.profile)

    if args.all_profiles:
        profiles = base_config.available_profiles()
        print(f"Running all profiles: {', '.join(profiles)}")
    else:
        profiles = [base_config.profile]

    failures = []

    for index, profile_name in enumerate(profiles):
        if index:
            print()
            print("=" * 72)
            print()

        try:
            run_profile(env_file, profile_name, args)
        except Exception as exc:
            if not args.all_profiles:
                raise
            failures.append((profile_name, str(exc)))
            print(f"ERROR [{profile_name}]: {exc}", file=sys.stderr)

    if failures:
        print()
        print("One or more profiles failed:", file=sys.stderr)
        for profile_name, message in failures:
            print(f"  {profile_name}: {message}", file=sys.stderr)
        raise RuntimeError(
            f"{len(failures)} of {len(profiles)} profiles failed"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
