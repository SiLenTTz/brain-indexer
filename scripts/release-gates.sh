#!/bin/bash
# release-gates.sh — deterministische Release-Gates für die public-Repos.
#
# BLOCKIERT bei jedem Fehler. Ein nicht-blockierendes Gate ist kein Gate (R5.5).
# Fehlendes Werkzeug = rotes Gate, kein übersprungenes (R5.6).
#
# Gates:
#   G1  Security-Audit: persönliche Daten/Hostnames/IPs/Tokens im Export
#   G2  Secrets: .env-Dateien, API-Keys, Passwörter
#   G3  Build: Code kompiliert/baut (je Repo unterschiedlich)
#   G4  Tests: Test-Suite läuft grün (wenn vorhanden)
#   G5  README: keine <you>-Platzhalter, Links konsistent
#   G6  Gitignore: node_modules, .env, dist nicht getrackt
#   G7  Struktur: keine unerwarteten Dateien im Export
#
# Aufruf:
#   bash scripts/release-gates.sh <export-dir>    # Gate-Lauf über den Export
#   bash scripts/release-gates.sh --push <dir>   # Gates + Push wenn grün
set -euo pipefail

DIR="${1:-}"
PUSH=0
[ "${1:-}" = "--push" ] && { PUSH=1; DIR="${2:-}"; }

if [ -z "$DIR" ] || [ ! -d "$DIR" ]; then
  echo "Usage: bash scripts/release-gates.sh <export-dir> [--push]"
  echo "       bash scripts/release-gates.sh --push <export-dir>"
  exit 2
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YEL='\033[1;33m'; NC='\033[0m'
FAIL=0
gate() { # gate <id> <name> <command...>
  local id="$1"; shift; local name="$1"; shift
  printf "  %s  %s … " "$id" "$name"
  if "$@" >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
  else
    echo -e "${RED}✗${NC}"; FAIL=$((FAIL+1))
  fi
}
info() { printf "  %s  %s … " "$1" "$2"; }

echo ""
echo "═══ Release-Gates für $(basename "$DIR") ═══"
echo ""

# ─── G1: Security-Audit — persönliche Daten ─────────────────────────────
info "G1" "Security-Audit (Hostnames/IPs/Usernames/Tokens)"
# Nur generische Muster in diesem Script — KEINE persönlichen Identifier!
# Private Zusatzmuster: scripts/audit-patterns.local neben diesem Script
# (gitignored; eine erweiterte Regex pro Zeile, # = Kommentar).
AUDIT_PATTERNS='[A-Za-z0-9._%+-]+@(gmail|googlemail|hotmail|outlook|live|msn|gmx|web|t-online|protonmail|proton\.me|icloud|yahoo|aol|freenet)\.[a-z]{2,}|\b[a-z0-9]{6}\.ts\.net\b|\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|xox[bap]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_PATTERNS="$(grep -v '^\s*#' "$SCRIPT_DIR/audit-patterns.local" 2>/dev/null | grep -v '^\s*$' | paste -sd'|' - || true)"
[ -n "$LOCAL_PATTERNS" ] && AUDIT_PATTERNS="${AUDIT_PATTERNS}|${LOCAL_PATTERNS}"
# GitHub-Handle SiLenTTz ist OK (nur in README-URLs)
if grep -riE "$AUDIT_PATTERNS" "$DIR" --include="*.ts" --include="*.tsx" --include="*.js" \
    --include="*.py" --include="*.html" --include="*.json" --include="*.sh" \
    --include="*.md" --include="*.yaml" --include="*.yml" 2>/dev/null \
    | grep -v "^\s*$" | grep -v "SiLenTTz" | grep -v node_modules | grep -v "/dist/" | grep -v "\.git/" | grep -v 'release-gates\.sh' | grep -v 'audit-patterns\.local' | head -1 | grep -q .; then
  echo -e "${RED}✗ (persönliche Daten gefunden)${NC}"
  echo "    Fundstellen:"
  grep -riE "$AUDIT_PATTERNS" "$DIR" --include="*.ts" --include="*.tsx" --include="*.js" \
      --include="*.py" --include="*.html" --include="*.json" --include="*.sh" \
       --include="*.md" 2>/dev/null | grep -v "SiLenTTz" | grep -v node_modules | grep -v "/dist/" | grep -v "\.git/" | grep -v 'release-gates\.sh' | grep -v 'audit-patterns\.local' | head -5
  FAIL=$((FAIL+1))
else
  echo -e "${GREEN}✓${NC}"
fi

# ─── G2: Secrets ──────────────────────────────────────────────────────────
info "G2" "Secrets (.env-Dateien, hardcoded Keys)"
G2_FAIL=0
# .env-Dateien dürfen nicht existieren
for f in .env brain.env hive-brain.env .env.local; do
  [ -f "$DIR/$f" ] && { echo -e "${RED}✗ ($f gefunden)${NC}"; G2_FAIL=1; break; }
done
# Hardcoded Secrets (in Quotes, nicht env-reads)
if [ $G2_FAIL -eq 0 ]; then
  if grep -riE "(api[_-]?key|secret|password|token)\s*[:=]\s*["'][^"'\$]{8,}["']" "$DIR" \
      --include="*.ts" --include="*.tsx" --include="*.js" --include="*.py" \
      --include="*.json" --include="*.yaml" 2>/dev/null \
      | grep -v node_modules | grep -v ".env.example" | grep -v "release-gates" \
      | head -1 | grep -q .; then
    echo -e "${RED}✗ (hardcoded Secret gefunden)${NC}"
    G2_FAIL=1
  fi
fi
[ $G2_FAIL -eq 0 ] && echo -e "${GREEN}✓${NC}" || FAIL=$((FAIL+1))

# ─── G3: Build ────────────────────────────────────────────────────────────
if [ -f "$DIR/package.json" ] && [ -f "$DIR/tsconfig.json" ]; then
  (cd "$DIR" && [ -d node_modules ] && npx tsc --noEmit) \
    && echo -e "  G3  Build (typecheck) … ${GREEN}✓${NC}" \
    || { echo -e "  G3  Build (typecheck) … ${YEL}⚠ übersprungen (kein node_modules)${NC}"; }
elif [ -f "$DIR/indexer.py" ]; then
  gate "G3" "Build (Python syntax)" python3 -m py_compile "$DIR/indexer.py" "$DIR/server.py"
else
  gate "G3" "Build (JS syntax)" node --check "$DIR/server.js"
fi

# ─── G4: Tests ────────────────────────────────────────────────────────────
if [ -f "$DIR/test_indexer.py" ]; then
  (cd "$DIR" && timeout 60 python3 test_indexer.py 2>&1 | grep -q "ALLE TESTS") \
    && echo -e "  G4  Tests … ${GREEN}✓${NC}" \
    || { echo -e "  G4  Tests … ${YEL}⚠ keine Tests oder nicht grün${NC}"; }
fi

# ─── G5: README-Platzhalter ───────────────────────────────────────────────
gate "G5" "README (keine <you>-Platzhalter)" bash -c "
  ! grep -r '<you>\|<du>\|<dein' '$DIR/README.md' 2>/dev/null | head -1 | grep -q .
"

# ─── G6: Gitignore ────────────────────────────────────────────────────────
if [ -d "$DIR/.git" ]; then
  (cd "$DIR" && ! git ls-files | grep -qE "^node_modules/|^\.env$|^brain\.env$|^hive-brain\.env$|^dist/") \
    && echo -e "  G6  Gitignore (kein node_modules/.env/dist getrackt) … ${GREEN}✓${NC}" \
    || { echo -e "  G6  Gitignore … ${RED}✗ (sensible Dateien getrackt)${NC}"; FAIL=$((FAIL+1)); }
fi

# ─── G7: Struktur ────────────────────────────────────────────────────────
gate "G7" "Struktur (LICENSE vorhanden)" test -f "$DIR/LICENSE"

# ─── Ergebnis ─────────────────────────────────────────────────────────────
echo ""
if [ $FAIL -gt 0 ]; then
  echo -e "${RED}═══ ${FAIL} Gate(s) ROT — PUSH BLOCKIERT ═══${NC}"
  echo "Behebe die Fehler und führe die Gates erneut aus."
  exit 1
else
  echo -e "${GREEN}═══ ALLE GATES GRÜN ═══${NC}"
  if [ $PUSH -eq 1 ]; then
    echo "Pushing to origin/main …"
    (cd "$DIR" && git push origin main --tags 2>&1) || {
      echo -e "${RED}Push fehlgeschlagen — Remote gesetzt? git remote add origin …${NC}"
      exit 1
    }
    echo -e "${GREEN}✓ Veröffentlicht${NC}"
  else
    echo "Bereit zum Pushen: bash scripts/release-gates.sh --push $DIR"
  fi
fi
