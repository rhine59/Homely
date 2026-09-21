#!/bin/bash
#
# publication-audit.sh
#
# Pre-publication security audit for the Homely Git repository.
# READ ONLY: does not modify history, delete files, commit or push.
#
set -u
REPO_DIR="${1:-$(pwd)}"
REPORT="${REPO_DIR}/publication-audit-report.txt"
GITLEAKS_REPORT="${REPO_DIR}/gitleaks-report.json"
BLOCKERS=0
WARNINGS=0
heading(){ printf '\n============================================================\n%s\n============================================================\n' "$1"; }
pass(){ printf 'PASS: %s\n' "$1"; }
warn(){ printf 'WARN: %s\n' "$1"; WARNINGS=$((WARNINGS+1)); }
fail(){ printf 'FAIL: %s\n' "$1"; BLOCKERS=$((BLOCKERS+1)); }
cd "$REPO_DIR" || exit 1
exec > >(tee "$REPORT") 2>&1
heading "Homely Publication Audit"
echo "Repository: $(pwd)"; echo "Date: $(date)"

heading "1. Git repository"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then pass "Directory is a Git working tree."; else fail "Not a Git repository."; exit 1; fi
echo "Branch: $(git branch --show-current 2>/dev/null || echo unknown)"
git remote -v 2>/dev/null | sed -E 's#(https://)[^/@]+:[^/@]+@#\1<REDACTED>@#g' || true

heading "2. Working tree status"
STATUS="$(git status --porcelain)"
if [[ -z "$STATUS" ]]; then pass "Working tree is clean."; else warn "Working tree contains changes."; git status --short; fi

heading "3. Tracked files"
git ls-files
SENSITIVE_FILE_REGEX='(^|/)(\.env|[^/]*\.env|[^/]*\.env\.save|[^/]*\.key|[^/]*key\.txt|[^/]*\.log|[^/]*\.err|[^/]*\.sqlite3|credentials?[^/]*|secrets?[^/]*)$'
TRACKED_SENSITIVE="$(git ls-files | grep -Ei "$SENSITIVE_FILE_REGEX" | grep -Ev '(^|/)[^/]*\.env\.sample$' || true)"
if [[ -n "$TRACKED_SENSITIVE" ]]; then fail "Potentially sensitive runtime files are tracked:"; echo "$TRACKED_SENSITIVE"; else pass "No obvious credential/runtime files are currently tracked."; fi

heading "4. Runtime/private files present locally"
PRIVATE_FILES="$(find . -path './.git' -prune -o -type f \( -name '*.env' -o -name '*.env.save' -o -name '*.key' -o -name '*key.txt' -o -name '*.log' -o -name '*.err' -o -name '*.sqlite3' \) ! -name '*.env.sample' -print 2>/dev/null || true)"
if [[ -n "$PRIVATE_FILES" ]]; then
 warn "Private/runtime files exist locally; checking ignore status:"
 while IFS= read -r FILE; do
  [[ -z "$FILE" ]] && continue; CLEAN="${FILE#./}"
  if git check-ignore -q "$CLEAN" 2>/dev/null; then echo "  IGNORED: $CLEAN"; elif git ls-files --error-unmatch "$CLEAN" >/dev/null 2>&1; then fail "$CLEAN is TRACKED by Git."; else fail "$CLEAN is not ignored by Git."; fi
 done <<< "$PRIVATE_FILES"
else pass "No obvious private/runtime files found locally."; fi

heading "5. .gitignore"
[[ -f .gitignore ]] && cat .gitignore || fail ".gitignore is missing."
RECOMMENDED_PATTERNS=("*.env" "!*.env.sample" "*.env.save" "*.log" "*.err" "*.key" "*key.txt" "*.sqlite3" "__pycache__/" ".DS_Store" "gitleaks-report.json" "publication-audit-report.txt")
echo; echo "Checking recommended ignore rules:"
for PATTERN in "${RECOMMENDED_PATTERNS[@]}"; do
 if [[ -f .gitignore ]] && grep -Fqx "$PATTERN" .gitignore; then echo "  OK      $PATTERN"; else echo "  MISSING $PATTERN"; WARNINGS=$((WARNINGS+1)); fi
done

heading "6. Current files: credential indicators"
echo "Reporting filenames/line numbers only; matching values are not printed."
CURRENT_MATCHES="$(grep -RIniE --exclude-dir=.git --exclude='publication-audit-report.txt' --exclude='gitleaks-report.json' --exclude='*.log' --exclude='*.err' 'password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization|bearer|smtp|homely_email|email_to' . 2>/dev/null | cut -d: -f1-2 | sort -u || true)"
if [[ -n "$CURRENT_MATCHES" ]]; then warn "Credential-related terms occur; variable names/docs may be expected:"; echo "$CURRENT_MATCHES"; else pass "No credential-related terms found."; fi

heading "7. Email-address review"
EMAIL_FILES="$(grep -RIlE --exclude-dir=.git --exclude='publication-audit-report.txt' --exclude='gitleaks-report.json' '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' . 2>/dev/null || true)"
if [[ -n "$EMAIL_FILES" ]]; then warn "Email addresses occur in these files; confirm they are examples or intentionally public:"; echo "$EMAIL_FILES"; else pass "No email addresses detected."; fi

heading "8. Git history: suspicious filenames"
HISTORICAL_FILES="$(git log --all --name-only --pretty=format: 2>/dev/null | sed '/^[[:space:]]*$/d' | sort -u | grep -Ei '(^|/)(\.env|.*\.env$|.*\.env\.save$|.*\.key$|.*key\.txt$|.*\.log$|.*\.err$|.*\.sqlite3$|credentials?|secrets?|archive\.zip$|save/)' || true)"
if [[ -n "$HISTORICAL_FILES" ]]; then warn "Potentially sensitive filenames have existed in history:"; echo "$HISTORICAL_FILES"; else pass "No obvious sensitive filenames found in Git history."; fi

heading "9. Historically deleted sensitive-looking files"
DELETED_SENSITIVE="$(git log --all --diff-filter=D --summary 2>/dev/null | sed -n 's/.*delete mode [0-9]* //p' | grep -Ei 'env|key|password|secret|token|credential|log|sqlite|archive' || true)"
if [[ -n "$DELETED_SENSITIVE" ]]; then warn "Sensitive-looking files have previously been deleted:"; echo "$DELETED_SENSITIVE"; else pass "No obvious sensitive-looking historical deletions detected."; fi

heading "10. Gitleaks full-history secret scan"
if command -v gitleaks >/dev/null 2>&1; then
 gitleaks version || true; rm -f "$GITLEAKS_REPORT"
 if gitleaks git . --report-format json --report-path "$GITLEAKS_REPORT" --redact >/dev/null 2>&1; then pass "Gitleaks found no recognised secrets in Git history."; else
  if [[ -s "$GITLEAKS_REPORT" ]]; then fail "Gitleaks reported findings. Review $GITLEAKS_REPORT before publication."; else fail "Gitleaks did not complete successfully; run: gitleaks git . --redact"; fi
 fi
else fail "Gitleaks is not installed. Install with: brew install gitleaks"; fi

heading "11. Obsolete/project-cleanup files"
for FILE in backup.sh homely.readme; do if git ls-files --error-unmatch "$FILE" >/dev/null 2>&1; then warn "$FILE is still tracked."; else echo "OK: $FILE is not tracked."; fi; done

heading "12. Archives and private-certificate-like files"
ARCHIVES="$(git ls-files | grep -Ei '\.(zip|tar|tgz|gz|7z|rar|p12|pfx|pem|der)$' || true)"
if [[ -n "$ARCHIVES" ]]; then warn "Review these files:"; echo "$ARCHIVES"; else pass "No archive/private-certificate files are currently tracked."; fi

heading "PUBLICATION AUDIT SUMMARY"
echo "Blocking findings: $BLOCKERS"; echo "Warnings: $WARNINGS"; echo
if [[ "$BLOCKERS" -gt 0 ]]; then echo "RESULT: NOT READY FOR PUBLICATION"; RESULT=1
elif [[ "$WARNINGS" -gt 0 ]]; then echo "RESULT: NO AUTOMATIC BLOCKERS, BUT MANUAL REVIEW REQUIRED"; RESULT=1
else echo "RESULT: AUTOMATED CHECKS PASSED"; RESULT=0; fi
echo; echo "Audit report: $REPORT"
[[ -f "$GITLEAKS_REPORT" ]] && echo "Gitleaks JSON report: $GITLEAKS_REPORT"
echo "This script has made no changes to the repository."
exit "$RESULT"
