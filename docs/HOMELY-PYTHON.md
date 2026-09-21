# Homely Python Implementation

## Purpose

`homely.py` is the production entry point for the Homely project. It automatically optimises domestic hot-water (DHW) heating schedules by combining live Homely configuration, Octopus Agile half-hour electricity prices and a solar-radiation forecast.

For each configured household/profile it:

1. authenticates to Homely using OAuth with PKCE;
2. discovers the Homely user, house, settings and live DHW schedule;
3. retrieves Octopus Agile prices for the target day;
4. retrieves Open-Meteo shortwave-radiation forecasts;
5. estimates PV generation and the portion of the DHW electrical load requiring grid electricity;
6. ranks half-hour periods by expected grid cost;
7. converts the winning periods into Homely schedule entries;
8. preserves the other weekdays' existing schedules;
9. optionally writes the revised target day to Homely; and
10. sends a detailed success, dry-run or failure email.

The checked-in `com.richard.homely.plist` runs `homely.py --all-profiles`, confirming `homely.py` as the current unattended production program.

## Repository Python files

### homely.py

Current production implementation and the program that should normally be run and scheduled.

### homely_agile_dhw.py

Earlier Agile/DHW scheduler retained for history/reference. It should not be used as the unattended production entry point unless deliberately testing that older implementation.

### homely_solar.py

Earlier solar-aware development version retained for reference.

### homely_with_logic_email.py

Earlier development version incorporating detailed logic email reporting. Its useful reporting ideas have been incorporated into the current `homely.py`.

These older scripts are deliberately retained until there is a separate decision to archive or remove them.

## End-to-end data flow

```text
homely.env
    |
    v
select profile(s)
    |
    v
Homely OAuth + PKCE
    |
    v
discover user + single house
    |
    v
read settings + live DHW schedule
    |
    +-----------------------+
    |                       |
    v                       v
Octopus Agile          Open-Meteo
half-hour prices       solar radiation
    |                       |
    +-----------+-----------+
                |
                v
      estimate solar PV output
                |
                v
      estimate DHW grid energy
                |
                v
       solar-adjusted price
                |
                v
      rank half-hour periods
                |
                v
        choose N best slots
                |
                v
       merge adjacent slots
                |
                v
 build target weekday entries
                |
                v
preserve other weekday entries
                |
          +-----+------+
          |            |
       dry run       live run
          |            |
          |            v
          |     setHotWaterSchedule
          |            |
          +-----+------+
                |
                v
       activity email report
```

## Configuration model

All application configuration is loaded from `homely.env`; the program intentionally does not use host environment variables for its application configuration.

`Config` supports shared values and profile-specific overrides. For a setting `KEY`, lookup order is:

```text
PROFILE.KEY
    ↓
KEY
    ↓
default, if supplied
```

A default profile can be specified with `DEFAULT_PROFILE`. `--profile NAME` overrides it, while `--all-profiles` discovers every profile containing `PROFILE.HOMELY_EMAIL`.

Important profile settings include Homely email/password, Octopus product and tariff, email recipient, number of DHW slots, latitude/longitude, PV array size, PV performance factor and DHW electrical load.

Credentials belong only in the local `homely.env`, which is excluded from Git.

## Homely authentication

`homely_login()` reproduces the Homely mobile application's OAuth authorization-code flow with PKCE.

`generate_pkce()` creates a random verifier and SHA-256 challenge. A random OAuth state is also generated.

The program POSTs credentials and OAuth parameters to Homely's authorization endpoint, verifies the returned state, extracts the authorization code, and exchanges it with the verifier for an access token.

The script checks that an access token was actually returned before continuing.

`direct_session()` creates a Requests session with `trust_env=False`, deliberately preventing proxy or other host-environment settings from unexpectedly changing these HTTP connections.

## Dynamic Homely discovery

The `HomelyClient` wraps calls to the Homely customer API.

`discover_identity()` first calls `userInfo`, then `getUser`, and obtains the associated house ID.

There is an important safety check: if an account contains more than one house, the program refuses to guess which one should be controlled.

`get_settings()` retrieves the installation settings and validates the Homely timezone with Python `ZoneInfo`.

`validate_dhw_settings()` confirms that Homely reports DHW capability and a valid DHW setpoint range.

## Reading the live schedule

`get_all_schedules()` obtains the complete Homely schedule.

`extract_hotwater_schedule()` locates the hot-water schedule and validates its structure. It refuses to guess if Homely returns multiple hot-water schedules.

Each schedule entry is normalised to:

```python
{
    "id": "...",
    "weekdayId": ...,
    "start": ...,
    "end": ...
}
```

Missing entry IDs are generated locally.

Reading the live schedule before changing it is important because the program changes only the target weekday and preserves the rest.

## Target date

By default the scheduler optimises **tomorrow**.

`--today` changes the target to the current date in the timezone reported by the Homely installation.

This makes the scheduler independent of the Mac's own timezone when deciding which Homely weekday is being modified.

## Octopus Agile prices

`fetch_agile_prices()` calls the Octopus Energy public tariff API using the configured product and tariff.

The requested local day is converted to UTC for the API query. Returned UTC timestamps are converted back into the Homely installation timezone.

Only slots whose local start date matches the requested target date are retained and they are sorted chronologically.

If no rates exist, the program reports that prices may not yet have been published rather than constructing a schedule from missing data.

## Solar forecast

`fetch_solar_forecast()` calls Open-Meteo for hourly `shortwave_radiation` in W/m².

The forecast is requested in UTC and interpolated onto the 30-minute periods needed by Agile.

The resulting structure provides a radiation estimate for every half-hour of the local target day.

## Solar model

`add_solar_weighting()` combines each Agile period with its forecast radiation.

For each half hour:

```text
solar_factor = clamp(radiation_Wm2 / 1000, 0, 1)

expected_solar_kW =
    SOLAR_ARRAY_KWP × solar_factor × SOLAR_PERFORMANCE

expected_solar_kWh =
    expected_solar_kW × 0.5

dhw_load_kWh =
    DHW_LOAD_KW × 0.5

grid_needed_kWh =
    max(dhw_load_kWh - expected_solar_kWh, 0)

expected_cost_p =
    grid_needed_kWh × Agile_price_p_per_kWh

adjusted_price =
    expected_cost_p / dhw_load_kWh
```

The adjusted price is therefore the effective grid cost per kWh of the fixed DHW workload after predicted solar contribution.

### Important modelling assumption

The current implementation assumes predicted PV is available to the DHW heat pump before considering other household electricity consumption.

It does **not** subtract house base load, EV charging, appliances or other simultaneous loads before allocating PV to DHW.

This is an explicit current limitation: sunny periods can be scored more favourably than reality if another household load is using the solar generation at the same time.

## Choosing DHW periods

`choose_cheapest()` selects the requested number of half-hour periods.

When solar weighting is present it ranks by `adjusted_price`; otherwise it can rank by the raw Agile price.

The selected slots are returned chronologically after ranking.

The slot count precedence is:

```text
--slots N
    ↓
PROFILE.AGILE_DHW_SLOTS
    ↓
AGILE_DHW_SLOTS
    ↓
4
```

`merge_adjacent()` combines contiguous winning half-hour periods into longer DHW windows. This avoids creating unnecessary adjacent schedule entries in Homely.

## Building the replacement schedule

`build_entries()` converts the selected windows into the minute-based representation required by Homely.

`replace_weekday()` creates the complete updated schedule by removing the old entries for only the target weekday and inserting the newly calculated entries.

Entries for the other six weekdays remain unchanged.

This is a significant safety property: the scheduler does not recreate the entire week's strategy from assumptions.

## Writing to Homely

In a live run, `set_hotwater_schedule()` sends the complete revised hot-water schedule using Homely's `setHotWaterSchedule` method.

With `--dry-run`, no schedule is written. All calculations and reporting are still performed so the proposed result can be inspected safely.

## Email reporting

Every profile run captures its console activity through the `Tee` helper while still displaying it on the terminal.

`send_activity_email()` sends both plain-text and HTML versions. The HTML includes an expandable explanation of the optimisation model generated by `generate_logic_html()`.

The report records the profile, status and detailed activity.

Possible statuses include:

- `AUTH TEST OK`
- `DRY RUN`
- `SCHEDULE UPDATED`
- `FAILED`

If normal processing fails, the script attempts to email the captured activity plus the exception. Failure of the failure-email itself does not hide the original application exception.

## Multi-profile operation

`--all-profiles` discovers every configured profile and runs them sequentially.

A failure in one profile is recorded while remaining profiles are attempted. At the end, any failures cause a non-success program exit.

This behaviour is particularly important for the scheduled LaunchAgent: one household problem does not prevent attempts for the other configured households, while monitoring can still detect that the overall run was not completely successful.

## Command-line modes

```bash
# Test authentication/discovery without scheduling
python homely.py --profile Richard --auth-test

# Calculate today's change without writing it
python homely.py --profile Richard --today --dry-run

# Calculate tomorrow's change without writing it
python homely.py --profile Richard --dry-run

# Override the configured number of half-hour slots
python homely.py --profile Richard --slots 6 --dry-run

# Update tomorrow for one profile
python homely.py --profile Richard

# Process all configured profiles
python homely.py --all-profiles
```

The unattended LaunchAgent uses the last form.

## Function and class reference

| Function/class | Responsibility |
|---|---|
| `Config` | Loads config and resolves profile/shared settings |
| `direct_session()` | Creates HTTP session isolated from host proxy settings |
| `base64url_no_padding()` | PKCE-safe Base64 helper |
| `generate_pkce()` | Creates OAuth verifier/challenge |
| `homely_login()` | Performs Homely OAuth/PKCE login |
| `HomelyClient` | Wraps Homely customer API operations |
| `make_entry_id()` | Creates IDs for new schedule entries |
| `extract_hotwater_schedule()` | Validates/extracts live DHW schedule |
| `validate_dhw_settings()` | Checks installation supports DHW |
| `requested_date()` | Chooses today or tomorrow in Homely timezone |
| `fetch_agile_prices()` | Retrieves target-day Agile prices |
| `fetch_solar_forecast()` | Retrieves/interpolates solar radiation |
| `add_solar_weighting()` | Calculates predicted solar/grid/cost metrics |
| `choose_cheapest()` | Selects best half-hour periods |
| `merge_adjacent()` | Converts adjacent periods into windows |
| `minutes_after_midnight()` | Converts times for Homely schedule format |
| `build_entries()` | Builds target weekday Homely entries |
| `replace_weekday()` | Preserves six weekdays and replaces one |
| `print_schedule()` | Displays schedule for reporting/debugging |
| `Tee` | Captures output while retaining terminal output |
| `email_recipients()` | Resolves report recipients |
| `generate_logic_html()` | Builds expandable model explanation |
| `send_activity_email()` | Sends activity report |
| `_run_profile_core()` | Executes optimisation workflow |
| `run_profile()` | Adds capture/email/error handling |
| `main()` | Parses CLI and coordinates profiles |

## Worked example

Assume a profile has:

```text
AGILE_DHW_SLOTS=2
SOLAR_ARRAY_KWP=5.0
SOLAR_PERFORMANCE=0.85
DHW_LOAD_KW=3.5
```

For a 30-minute period with forecast radiation of 600 W/m²:

```text
solar_factor = 600 / 1000 = 0.60

expected_solar_kW =
    5.0 × 0.60 × 0.85
    = 2.55 kW

expected_solar_kWh =
    2.55 × 0.5
    = 1.275 kWh

DHW load =
    3.5 × 0.5
    = 1.75 kWh

grid needed =
    1.75 - 1.275
    = 0.475 kWh
```

If Agile costs 20 p/kWh:

```text
expected grid cost =
    0.475 × 20
    = 9.5 p

adjusted price =
    9.5 / 1.75
    = 5.43 p/kWh
```

A different slot might have a lower raw Agile price but little solar and therefore a higher effective cost for the DHW workload. The scheduler compares these adjusted values across the day, selects the configured number of best half-hours, merges adjacent winners, replaces only the target weekday in the live Homely schedule, and sends the result in the activity email.

## Safety features

The current implementation deliberately avoids several potentially dangerous assumptions:

- it will not guess between multiple Homely houses;
- it will not guess between multiple hot-water schedules;
- it validates DHW support before modifying schedules;
- it preserves six non-target weekdays;
- it supports a full dry-run mode;
- OAuth state is verified;
- target dates use the Homely timezone;
- missing Agile data produces an error rather than an invented schedule;
- multi-profile failures produce an overall failure exit status.

For installation and unattended scheduling, see [Installation and Configuration](INSTALLATION.md).
