# Public Repository Release and Security Audit

## Purpose

Before changing the Homely repository from private to public, perform a security and publication audit of both the **current working tree** and the **entire Git history**.

A clean current branch is not sufficient evidence that a repository is safe to publish: credentials deleted in a later commit can remain recoverable from earlier Git objects.

The repository therefore contains a read-only audit tool:

```text
scripts/publication-audit.sh
```

It does not delete files, rewrite history, commit, push, change repository visibility, or deliberately print detected secret values.

## Quick start

Clone/update the repository on the Mac that contains the full Git history:

```bash
cd ~/scripts/Homely
git pull

chmod +x scripts/publication-audit.sh
./scripts/publication-audit.sh
```

For the full history secret scan, install Gitleaks first:

```bash
brew install gitleaks
```

Then rerun the audit.

## What the audit checks

### 1. Git repository and remote

Confirms the directory is a Git working tree and reports the current branch and configured remotes. Credentials embedded in HTTPS remote URLs are redacted from the report.

### 2. Working tree status

Reports whether local changes/untracked files exist.

A dirty working tree is not automatically a security problem, but it means the exact content being audited may differ from the committed GitHub version.

### 3. Tracked files

Lists Git-tracked files and checks for filenames commonly associated with private runtime data, including:

- `.env`;
- `*.env`;
- `*.env.save`;
- `*.key`;
- `*key.txt`;
- `*.log`;
- `*.err`;
- `*.sqlite3`;
- credential/secret-named files.

`*.env.sample` is allowed because it should contain placeholders rather than credentials.

### 4. Private/runtime files on the local machine

Looks for local environment, key, log and database files.

Their existence locally can be legitimate. The important check is whether Git ignores them.

Each detected file is classified as ignored, tracked, or dangerously unignored.

### 5. .gitignore protection

The audit expects protection for:

```gitignore
*.env
!*.env.sample
*.env.save
*.log
*.err
*.key
*key.txt
*.sqlite3
__pycache__/
.DS_Store
gitleaks-report.json
publication-audit-report.txt
```

Missing rules are warnings requiring review.

### 6. Credential indicators in current files

Searches current repository files for terms such as password, token, API key, bearer authorization and SMTP configuration.

Because legitimate source code and documentation necessarily contain names such as `HOMELY_PASSWORD`, these matches are warnings rather than proof of a leak.

For safety, the audit reports filenames and line numbers rather than the matching lines themselves.

### 7. Email-address review

Identifies files containing email-address-shaped strings.

Example addresses such as `user@example.com` are appropriate. Personal addresses should be reviewed before publication unless deliberately intended to be public.

### 8. Suspicious filenames in Git history

Uses `git log --all` to identify environment, key, log, database, archive or secret-looking files that existed in earlier commits.

Finding a filename does not prove that it contained a credential, but it is an important signal for deeper inspection.

### 9. Historically deleted files

Specifically checks deleted historical paths for names associated with credentials and private runtime information.

Deleting a sensitive file in a later commit does not remove it from earlier Git history.

### 10. Gitleaks full-history scan

Gitleaks is used to scan the complete Git history for recognised credential patterns.

The audit runs:

```bash
gitleaks git . \
    --report-format json \
    --report-path gitleaks-report.json \
    --redact
```

`--redact` is deliberately used so reports do not unnecessarily expose detected credential values.

Install Gitleaks on macOS with:

```bash
brew install gitleaks
```

A Gitleaks finding is a publication blocker until investigated.

### 11. Obsolete files

The audit currently warns if these migration/development artefacts remain tracked:

```text
backup.sh
homely.readme
```

They are not automatically deleted because the audit is intentionally read-only.

### 12. Archives and certificate/key-like files

Checks tracked filenames for:

```text
.zip .tar .tgz .gz .7z .rar
.p12 .pfx .pem .der
```

Such files require manual review before publication.

## Audit output

The script creates:

```text
publication-audit-report.txt
```

and, when Gitleaks generates a report:

```text
gitleaks-report.json
```

Both should remain untracked.

The final result is one of:

### AUTOMATED CHECKS PASSED

No automatic blockers or warnings were detected. A final human review is still appropriate.

### NO AUTOMATIC BLOCKERS, BUT MANUAL REVIEW REQUIRED

No definite secret leak was identified, but one or more items require inspection.

### NOT READY FOR PUBLICATION

At least one blocking condition exists, such as a tracked sensitive runtime file or a Gitleaks finding.

Do not change repository visibility until blockers are resolved.

## If Gitleaks finds a secret

Do **not** make the repository public.

Do not copy an unredacted password/token into an issue, documentation, commit, email or ChatGPT conversation.

Record instead:

- finding type/rule;
- filename;
- commit SHA;
- whether the credential is still active;
- a redacted representation if identification is necessary.

The first security action for a genuine exposed credential is normally to **revoke or rotate it**.

Deleting the file in a new commit is not sufficient because the original value remains in Git history.

After rotation, decide whether the repository history also needs rewriting before publication.

## Manual review before publication

Even when automated checks pass, inspect:

```bash
git status
git diff
git ls-files
```

Review at least:

- `README.md`;
- `docs/`;
- `homely.env.sample`;
- all Python source;
- shell scripts;
- plist files;
- historical/development source retained in the repository.

Look for information that automated secret scanners may not classify as credentials, such as:

- personal email addresses;
- home coordinates;
- names that should remain private;
- Homely user or house IDs;
- account numbers;
- private hostnames/IP addresses;
- telephone numbers;
- personally identifying log examples.

## Current Homely credential design

The intended public repository contains configuration **names and placeholders**, not live values.

Live configuration belongs in:

```text
homely.env
```

on the local machine.

The public template is:

```text
homely.env.sample
```

Source code may legitimately contain symbolic names such as:

```text
HOMELY_EMAIL
HOMELY_PASSWORD
EMAIL_PASSWORD
Authorization
access_token
```

Their presence is not itself a credential disclosure. The audit is concerned with real values.

## Recommended publication process

1. Pull the latest repository.
2. Ensure Gitleaks is installed.
3. Run `scripts/publication-audit.sh`.
4. Resolve every blocker.
5. Review every warning.
6. Rotate any credential that was genuinely committed.
7. Rewrite history if required.
8. Run the audit again.
9. Review the final tracked file list manually.
10. Commit/push any cleanup.
11. Run the audit once more against the final state.
12. Only then change the repository visibility to public.

## Current release policy

A repository should not be considered ready merely because the current branch contains no obvious passwords.

For Homely, the publication gate is:

```text
current files clean
        +
private runtime files ignored
        +
Git history scanned
        +
Gitleaks clear
        +
warnings manually reviewed
        +
final human inspection
        =
candidate for public release
```

This process should be repeated whenever substantial credential/configuration changes are made before a future public release.
