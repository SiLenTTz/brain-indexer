---
name: release-gates
description: Führt die deterministischen Release-Gates vor jedem Public-Push aus. Aktiv wenn committet oder gepusht werden soll, oder wenn gefragt wird "ist der Release sauber?". Blockiert bei jedem Fehler.
---

# Skill: release-gates

Eine Definition von "sicher veröffentlichen", für Mensch und Agent.
Dieselben Gates laufen als `scripts/release-gates.sh` — das Skript ist die
Ausführung, dieser Skill die Erklärung. Es gibt keinen zweiten Regelsatz.

## Die Gates **blockieren**

Ein nicht-blockierendes Gate ist kein Gate. `--push` ohne `--gates` gibt es nicht.

## Ausführen

```bash
# Gates über den Export laufen lassen (ohne Push)
bash scripts/release-gates.sh .

# Gates + Push wenn alles grün ist
bash scripts/release-gates.sh --push .
```

## Die Spalten

| ID | Gate | Was geprüft wird |
| --- | --- | --- |
| G1 | Security-Audit | Persönliche Daten: Hostnames (tail*.ts.net), IPs (100.x.x.x), Usernames, Tokens in Code-Dateien |
| G2 | Secrets | `.env`-Dateien, API-Keys, Passwörter in Quelldateien |
| G3 | Build | TypeScript: `tsc --noEmit` · Python: `py_compile` · JS: `node --check` |
| G4 | Tests | Test-Suite (wenn vorhanden) muss grün sein |
| G5 | README | Keine `<you>`/`<du>`-Platzhalter mehr |
| G6 | Gitignore | Kein `node_modules`, `.env`, `dist` im Git-Tracking |
| G7 | Struktur | LICENSE vorhanden |

## Wenn ein Gate rot ist

1. **Nicht umgehen.** Das Gate ist rot, weil etwas nicht stimmt.
2. **Fehler beheben** (nicht das Gate abschwächen).
3. **Erneut ausführen** bis grün.
4. **Fehlendes Werkzeug ist rot** — nicht übersprungen (z.B. kein node_modules = kein Typecheck = rot).

## Vor dem ersten Push

```bash
cd <export-dir>
git remote add origin git@github.com:SiLenTTz/<repo-name>.git  # nur 1×
bash scripts/release-gates.sh --push .
```
