# Homely

Homely is an automated domestic-hot-water scheduler that combines **Homely heat-pump control**, **Octopus Agile half-hour electricity prices** and **forecast solar generation**.

The current production program is `homely.py`. It supports multiple household profiles, dynamically discovers each Homely installation, calculates the lowest expected grid-cost DHW periods, safely replaces only the target weekday's hot-water schedule and emails a detailed activity report.

## Documentation

**[Homely Python Implementation](docs/HOMELY-PYTHON.md)** — architecture, Homely OAuth/API interaction, Agile retrieval, solar model, slot ranking, schedule construction, email reporting, safety behaviour and function reference.

**[Installation and Configuration](docs/INSTALLATION.md)** — Python setup, `homely.env`, profile configuration, safe first tests, live operation, LaunchAgent installation and troubleshooting.

**[API Reference](docs/API-REFERENCE.md)** — all Homely API calls discovered and used by the project, request examples, likely response structures, OAuth flow, customer methods, plus the Octopus Agile and Open-Meteo calls used by the scheduler.

## Main files

- `homely.py` — current production scheduler.
- `homely.env.sample` — example multi-profile configuration.
- `com.richard.homely.plist` — macOS LaunchAgent definition.
- `launchd_setup.sh` — LaunchAgent installer/reinstaller.
- `requirements.txt` — Python dependencies.
- `docs/` — current technical and operational documentation.

## Historical/development implementations

The repository also retains:

- `homely_agile_dhw.py`
- `homely_solar.py`
- `homely_with_logic_email.py`

These are earlier development implementations retained for reference. The scheduled production entry point is `homely.py`.

## Safe test sequence

```bash
# Authentication and Homely discovery only
python homely.py --profile Richard --auth-test

# Calculate tomorrow's schedule without changing Homely
python homely.py --profile Richard --dry-run

# Test all configured profiles without changing Homely
python homely.py --all-profiles --dry-run
```

Only remove `--dry-run` after reviewing the proposed schedule.

## Unattended operation

The supplied LaunchAgent runs:

```bash
python homely.py --all-profiles
```

daily at 23:00.

See [Installation and Configuration](docs/INSTALLATION.md) before enabling unattended operation.

## Security

`homely.env` contains Homely and email credentials and must never be committed. Runtime environment files and logs are excluded from Git.
