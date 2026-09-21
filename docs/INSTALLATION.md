# Homely Installation and Configuration

## 1. Intended standalone layout

The Homely repository is intended to be cloned as:

```text
/Users/richardhine/scripts/Homely
```

The supplied LaunchAgent is configured for this location and for the existing pyenv interpreter:

```text
/Users/richardhine/.pyenv/versions/octopus/bin/python
```

If either location differs, update the plist before installing it.

## 2. Clone

```bash
cd ~
git clone git@github.com:rhine59/Homely.git
cd Homely
```

## 3. Python dependencies

The application requires `requests` and `python-dotenv`.

Using the existing `octopus` pyenv environment:

```bash
~/.pyenv/versions/octopus/bin/python -m pip install -r requirements.txt
```

Verify:

```bash
~/.pyenv/versions/octopus/bin/python -c 'import requests, dotenv; print("Dependencies OK")'
```

## 4. Configuration

Copy the sample:

```bash
cp homely.env.sample homely.env
```

Edit `homely.env` locally. Never commit it.

The application reads this file directly and intentionally does not rely on host environment variables for its application configuration.

A representative configuration is:

```dotenv
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
EMAIL_FROM=sender@example.com
EMAIL_FROM_NAME="Revised Hot Water Heating schedule"
EMAIL_PASSWORD=app-password

DEFAULT_PROFILE=Richard
AGILE_DHW_SLOTS=2

Richard.HOMELY_EMAIL=homely-login@example.com
Richard.HOMELY_PASSWORD=homely-password
Richard.EMAIL_TO=report@example.com

Richard.OCTOPUS_PRODUCT=AGILE-...
Richard.OCTOPUS_TARIFF=E-1R-AGILE-...

Richard.LAT=...
Richard.LON=...
Richard.SOLAR_ARRAY_KWP=5.0
Richard.SOLAR_PERFORMANCE=0.85
Richard.DHW_LOAD_KW=3.5
```

Use actual values from the installation; do not use the example values as credentials.

## 5. Configuration precedence

Profile-specific values override shared values:

```text
Richard.KEY
    ↓
KEY
    ↓
program default
```

The number of DHW half-hour periods additionally allows a command-line override:

```text
--slots N
    ↓
Richard.AGILE_DHW_SLOTS
    ↓
AGILE_DHW_SLOTS
    ↓
4
```

## 6. First tests

Start with authentication/discovery only:

```bash
~/.pyenv/versions/octopus/bin/python homely.py --profile Richard --auth-test
```

Then calculate a schedule without writing anything:

```bash
~/.pyenv/versions/octopus/bin/python homely.py --profile Richard --dry-run
```

To test today's prices rather than tomorrow:

```bash
~/.pyenv/versions/octopus/bin/python homely.py --profile Richard --today --dry-run
```

Only after inspecting the proposed schedule should you perform a live one-profile update:

```bash
~/.pyenv/versions/octopus/bin/python homely.py --profile Richard
```

Finally test the same mode used by unattended scheduling:

```bash
~/.pyenv/versions/octopus/bin/python homely.py --all-profiles --dry-run
```

and then, when satisfied:

```bash
~/.pyenv/versions/octopus/bin/python homely.py --all-profiles
```

## 7. What a normal run needs externally

A live run needs network access to:

- Homely OAuth/customer services for authentication, settings, schedules and updates;
- Octopus Energy's tariff API for Agile rates;
- Open-Meteo for solar radiation;
- the configured SMTP service for activity email.

Failure of required source data stops that profile rather than silently inventing a schedule.

## 8. Email reports

Each profile sends an activity email after processing. The email contains the captured console report and an expandable explanation of the scheduling logic.

The recipient comes from `PROFILE.EMAIL_TO`. Where supported by the application configuration, shared SMTP values can be reused across profiles.

The script also attempts to send a `FAILED` activity report when profile processing raises an exception.

## 9. LaunchAgent

The repository contains:

```text
com.richard.homely.plist
```

It runs:

```text
/Users/richardhine/.pyenv/versions/octopus/bin/python
/Users/richardhine/scripts/Homely/homely.py
--all-profiles
```

at **23:00 each day**.

The working directory is:

```text
/Users/richardhine/scripts/Homely
```

and logs are written there as:

```text
homely.log
homely.err
```

Validate before installation:

```bash
plutil -lint com.richard.homely.plist
```

## 10. Install/reinstall launchd

The repository's `launchd_setup.sh` uses the modern per-user LaunchAgent workflow.

```bash
chmod +x launchd_setup.sh
./launchd_setup.sh
```

It validates and copies the plist to `~/Library/LaunchAgents`, removes an existing loaded instance when necessary, bootstraps the new one and prints its state.

Force an immediate test:

```bash
launchctl kickstart -k gui/$(id -u)/com.richard.homely
```

Inspect:

```bash
launchctl print gui/$(id -u)/com.richard.homely
```

Unload:

```bash
launchctl bootout gui/$(id -u)/com.richard.homely
```

## 11. Troubleshooting

### Config file not found

By default `homely.py` expects `homely.env` beside the script. You can explicitly select another file with `--env-file`.

### Homely login fails

Check the profile's `HOMELY_EMAIL` and `HOMELY_PASSWORD`. The application uses Homely OAuth with PKCE; it does not simply call the customer API with the password.

### Multiple houses

The program intentionally stops rather than guessing which Homely house to control. Code/configuration would need an explicit house-selection mechanism before supporting such an account.

### No Agile prices

Tomorrow's rates may not yet have been published. The application does not manufacture fallback rates.

### Solar forecast error

Check `LAT`, `LON` and network access to Open-Meteo.

### Email failure

Check `SMTP_SERVER`, `SMTP_PORT`, `EMAIL_FROM`, `EMAIL_PASSWORD` and recipients. The current implementation uses SMTP over SSL.

### Works manually but not from launchd

Check the absolute paths in the plist first. Then run exactly the command specified by `ProgramArguments` from a terminal.

Also inspect:

```bash
tail -100 ~/scripts/Homely/homely.log
tail -100 ~/scripts/Homely/homely.err
```

### Repository was moved

Update all occurrences of the old location in `com.richard.homely.plist`, reinstall the LaunchAgent, and verify with `launchctl print`.

## 12. Security

Never commit:

- `homely.env`;
- Homely passwords;
- SMTP/app passwords;
- logs containing sensitive operational information;
- temporary credential files.

The repository `.gitignore` protects the normal runtime files, but always inspect `git status` before committing.

If a credential was ever committed, deleting it in a later commit does not erase Git history. Rotate the credential.

## 13. Operational checklist

Before relying on unattended operation:

- standalone repository cloned to its permanent location;
- correct Python interpreter confirmed;
- dependencies installed;
- `homely.env` created locally;
- each profile passes `--auth-test`;
- each profile has been inspected with `--dry-run`;
- Agile product/tariff values verified;
- PV and DHW model values verified;
- email delivery verified;
- a live single-profile schedule update tested;
- `--all-profiles --dry-run` tested;
- plist passes `plutil -lint`;
- LaunchAgent installed;
- first scheduled run checked in logs and email.

For the scheduling model and code architecture, see [Homely Python Implementation](HOMELY-PYTHON.md).
