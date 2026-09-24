#!/bin/bash
# Aktiviert .githooks als Hook-Pfad für dieses Repo (pre-push Checks).
set -e
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
echo "Git-Hooks aktiviert: .githooks/pre-push läuft ab jetzt vor jedem Push."
