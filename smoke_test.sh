#!/usr/bin/env bash
# smoke_test.sh — verify every component of fitcast end-to-end.
#
# Usage:
#   ./smoke_test.sh           # free checks only (no API spend)
#   ./smoke_test.sh --paid    # also run paid checks (~$0.20 in API spend)
#
# Run from the project root with your venv activated.

set -uo pipefail

PAID=false
[[ "${1:-}" == "--paid" ]] && PAID=true

PASS=0
FAIL=0
WARN=0
FAILED_CHECKS=()

# ─── Helpers ─────────────────────────────────────────────────────────────

_green=$'\033[32m'
_red=$'\033[31m'
_yellow=$'\033[33m'
_dim=$'\033[2m'
_bold=$'\033[1m'
_reset=$'\033[0m'

section() { printf '\n%s── %s ──%s\n' "$_bold" "$1" "$_reset"; }
ok()      { printf '  %s✓%s %s\n' "$_green" "$_reset" "$1"; PASS=$((PASS+1)); }
fail()    { printf '  %s✗%s %s\n' "$_red" "$_reset" "$1"; FAIL=$((FAIL+1)); FAILED_CHECKS+=("$1"); }
warn()    { printf '  %s!%s %s\n' "$_yellow" "$_reset" "$1"; WARN=$((WARN+1)); }
info()    { printf '  %s%s%s\n' "$_dim" "$1" "$_reset"; }

# ─── 1. Environment ───────────────────────────────────────────────────────

section "1. Environment"

PY=${PYTHON:-python3.11}
if ! command -v "$PY" >/dev/null 2>&1; then
    fail "Python: $PY not found. Set PYTHON=<your-python> if it's elsewhere."
    echo "Aborting — can't run further tests without Python." >&2
    exit 1
fi
PY_VER=$("$PY" --version 2>&1)
ok "Python: $PY_VER"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    warn "No virtualenv active — recommended to source .venv/bin/activate first"
else
    info "Virtualenv: $VIRTUAL_ENV"
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    if $PAID; then
        fail "ANTHROPIC_API_KEY is unset — paid checks will fail. export it first."
    else
        warn "ANTHROPIC_API_KEY is unset — needed for any paid API check"
    fi
else
    info "ANTHROPIC_API_KEY: set (${#ANTHROPIC_API_KEY} chars)"
fi

# ─── 2. Dependencies ─────────────────────────────────────────────────────

section "2. Dependencies importable"

check_import() {
    local name="$1" import="${2:-$1}"
    if "$PY" -c "import $import" 2>/dev/null; then
        ok "$name"
    else
        fail "$name not installed (pip install -r requirements.txt)"
    fi
}

check_import anthropic
check_import pydantic
check_import requests
check_import "PyYAML" yaml
check_import "PyPDF2"
check_import "python-docx" docx
check_import "sentence-transformers" sentence_transformers

# ─── 3. Project files ────────────────────────────────────────────────────

section "3. Project files"

for f in pipeline.py audit.py tailor.py track.py extract_keywords.py \
         bootstrap_companies.py bootstrap_ontologies.py skill_extractor.py \
         check_resume_format.py compare_resumes.py \
         config.yaml requirements.txt resume.example.md; do
    if [[ -f "$f" ]]; then
        ok "$f present"
    else
        fail "$f missing"
    fi
done

if [[ -f resume.md ]]; then
    info "resume.md present ($(wc -c < resume.md | tr -d ' ') chars)"
else
    warn "resume.md not present — pipeline will fail. Copy from resume.example.md."
fi

# ─── 4. Compile all Python ───────────────────────────────────────────────

section "4. Python syntax"

for f in pipeline.py audit.py tailor.py track.py extract_keywords.py \
         bootstrap_companies.py bootstrap_ontologies.py skill_extractor.py \
         check_resume_format.py compare_resumes.py; do
    [[ -f "$f" ]] || continue
    if "$PY" -m py_compile "$f" 2>/dev/null; then
        ok "$f compiles"
    else
        fail "$f syntax error"
    fi
done

# ─── 5. Unit tests ───────────────────────────────────────────────────────

section "5. Unit tests (pytest)"

if "$PY" -c "import pytest" 2>/dev/null; then
    if "$PY" -m pytest tests/ -q --no-header 2>&1 | tee /tmp/fitcast_pytest.log | tail -3; then
        TEST_RESULT=$(grep -E "passed|failed|error" /tmp/fitcast_pytest.log | tail -1)
        ok "pytest: $TEST_RESULT"
    else
        fail "pytest failed — see output above"
    fi
else
    warn "pytest not installed — skipping (pip install pytest)"
fi

# ─── 6. Skill extractor ──────────────────────────────────────────────────

section "6. Skill extractor"

if [[ ! -f data/skills.json ]]; then
    warn "data/skills.json missing — run python bootstrap_ontologies.py"
else
    SKILL_COUNT=$("$PY" -c "import json; print(json.load(open('data/skills.json'))['skill_count'])" 2>/dev/null || echo "?")
    ok "data/skills.json loaded ($SKILL_COUNT skills)"

    if [[ -f resume.md ]]; then
        N=$("$PY" skill_extractor.py < resume.md 2>/dev/null | grep -c "  " || echo "0")
        if [[ "$N" -gt 0 ]]; then
            ok "skill_extractor extracted $N skills from your resume"
        else
            warn "skill_extractor extracted 0 skills — check resume.md content"
        fi
    fi
fi

# ─── 7. Network: source APIs reachable ───────────────────────────────────

section "7. Source APIs reachable"

probe() {
    local label="$1" url="$2" expect="${3:-200}"
    local code
    code=$(curl -sL -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null)
    if [[ "$code" == "$expect" ]]; then
        ok "$label (HTTP $code)"
    else
        fail "$label returned HTTP $code (expected $expect)"
    fi
}

probe "Greenhouse" "https://boards-api.greenhouse.io/v1/boards/recursionpharmaceuticals/jobs"
probe "Lever"      "https://api.lever.co/v0/postings/mistral?mode=json"
probe "Ashby"      "https://api.ashbyhq.com/posting-api/job-board/linear"
probe "The Muse"   "https://www.themuse.com/api/public/jobs?page=0"
probe "O*NET dl"   "https://www.onetcenter.org/dl_files/database/db_28_3_text.zip"

# ─── 8. Config validates ─────────────────────────────────────────────────

section "8. config.yaml validates"

if "$PY" -c "import yaml; c = yaml.safe_load(open('config.yaml')); assert c is not None" 2>/dev/null; then
    ok "config.yaml is valid YAML"
    "$PY" -c "
import yaml
c = yaml.safe_load(open('config.yaml')) or {}
gh = ((c.get('greenhouse') or {}).get('companies')) or []
lever = ((c.get('lever') or {}).get('companies')) or []
ashby = ((c.get('ashby') or {}).get('companies')) or []
muse = bool(c.get('muse'))
print(f'    greenhouse: {len(gh)} companies')
print(f'    lever:      {len(lever)} companies')
print(f'    ashby:      {len(ashby)} companies')
print(f'    muse:       {\"on\" if muse else \"off\"}')
print(f'    keywords:   {len(c.get(\"keywords\") or [])}')
print(f'    max_jobs:   {c.get(\"max_jobs\", \"?\")}')
"
else
    fail "config.yaml is malformed"
fi

# ─── 9. State files (clean handling) ─────────────────────────────────────

section "9. State file handling"

# These files are gitignored — should be readable if present, regenerable if not
for f in seen.json applied.json companies.bootstrap.yaml; do
    if [[ -f "$f" ]]; then
        info "$f present ($(wc -c < "$f" | tr -d ' ') chars)"
    else
        info "$f not present (will be auto-created when relevant)"
    fi
done

# ─── 10. Paid checks (optional) ──────────────────────────────────────────

if $PAID; then
    section "10. Paid API checks (~\$0.20 total)"

    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        fail "skipping paid checks — ANTHROPIC_API_KEY not set"
    elif [[ ! -f resume.md ]]; then
        fail "skipping paid checks — resume.md not present"
    else
        # 10a. dry-run (Haiku prerank only, no Sonnet)
        info "10a. python pipeline.py --dry-run (~\$0.07 — Haiku prerank only)"
        if "$PY" pipeline.py --dry-run >/tmp/fitcast_dryrun.log 2>&1; then
            COUNT=$(grep -c "^  \[" /tmp/fitcast_dryrun.log || echo 0)
            ok "dry-run completed — would have analyzed $COUNT jobs"
        else
            fail "dry-run failed — see /tmp/fitcast_dryrun.log"
        fi

        # 10b. min real run (1 deep analysis)
        info "10b. python pipeline.py --max-jobs 1 --include-seen (~\$0.03 — 1 deep analysis)"
        if "$PY" pipeline.py --max-jobs 1 --include-seen >/tmp/fitcast_run.log 2>&1; then
            if [[ -f results.csv ]] && [[ $(wc -l < results.csv) -gt 1 ]]; then
                ok "pipeline ran end-to-end, results.csv has data"
            else
                fail "pipeline ran but results.csv empty/missing"
            fi
        else
            fail "pipeline run failed — see /tmp/fitcast_run.log"
        fi

        # 10c. extract_keywords (one Sonnet call, ~$0.02)
        info "10c. python extract_keywords.py (~\$0.02)"
        if "$PY" extract_keywords.py >/tmp/fitcast_kw.log 2>&1; then
            ok "extract_keywords.py ran"
        else
            fail "extract_keywords.py failed — see /tmp/fitcast_kw.log"
        fi

        # 10d. audit on the first result (~$0.04)
        if [[ -f results.json ]]; then
            FIRST_URL=$("$PY" -c "import json; r = json.load(open('results.json')); print(r[0]['url'] if r else '')" 2>/dev/null)
            if [[ -n "$FIRST_URL" ]]; then
                info "10d. python audit.py <first-result> (~\$0.04)"
                if "$PY" audit.py "$FIRST_URL" >/tmp/fitcast_audit.log 2>&1; then
                    ok "audit.py ran on $FIRST_URL"
                else
                    fail "audit.py failed — see /tmp/fitcast_audit.log"
                fi
            fi
        fi
    fi
else
    section "10. Paid checks (skipped — re-run with --paid to include)"
    info "Skipped: dry-run, real pipeline, extract_keywords, audit"
    info "These would total ~\$0.20 in Anthropic API spend"
fi

# ─── Summary ─────────────────────────────────────────────────────────────

printf '\n%s═══ Summary ═══%s\n' "$_bold" "$_reset"
printf '  %sPassed:%s %d\n' "$_green" "$_reset" "$PASS"
printf '  %sWarnings:%s %d\n' "$_yellow" "$_reset" "$WARN"
printf '  %sFailed:%s %d\n' "$_red" "$_reset" "$FAIL"

if [[ $FAIL -gt 0 ]]; then
    printf '\n%sFailed checks:%s\n' "$_red" "$_reset"
    for c in "${FAILED_CHECKS[@]}"; do
        printf '  • %s\n' "$c"
    done
    exit 1
fi

printf '\n%sAll checks passed.%s'  "$_green" "$_reset"
if ! $PAID; then
    printf ' Re-run with %s./smoke_test.sh --paid%s for end-to-end validation (~\$0.20).' "$_bold" "$_reset"
fi
printf '\n'
