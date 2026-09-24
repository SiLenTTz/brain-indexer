#!/bin/bash
# export-public.sh — sauberer, veröffentlichbarer Export (frische Historie),
# wiederholbar: privates Repo weiterentwickeln, vor jedem Release erneut laufen
# lassen. Aufruf: bash export-public.sh [ziel] [release-name]
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${1:-$SRC/../brain-indexer-public}"
EXCLUDE=(.git brain.env __pycache__ legacy *.pyc scripts/audit-patterns.local)
mkdir -p "$DST"
rsync -a --delete "${EXCLUDE[@]/#/--exclude=}" "$SRC"/ "$DST"/
cd "$DST"
REL="${2:-}"
if [ ! -d .git ]; then
  git init -q -b main
  git add -A
  git -c user.name=SiLenTTz -c user.email=SiLenTTz@users.noreply.github.com \
    commit -q -m "Initial public release — deterministic markdown indexer (SQLite + LOD API + SSE)"
  [ -n "$REL" ] && git tag -a "$REL" -m "$REL"
else
  git add -A
  if git diff --cached --quiet; then
    echo "Keine Änderungen seit dem letzten Export."
  else
    git -c user.name=SiLenTTz -c user.email=SiLenTTz@users.noreply.github.com \
      commit -q -m "${REL:-Release $(date +%Y-%m-%d)}"
    [ -n "$REL" ] && git tag -a "$REL" -m "$REL"
    echo "Release committet${REL:+ (+ Tag $REL)}."
  fi
fi
echo "Export: $DST — vor dem Push selbst auditieren, dann:"
echo "  git remote add origin https://github.com/SiLenTTz/brain-indexer.git   # nur 1x"
echo "  git push origin main --tags"
