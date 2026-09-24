# Contributing

Danke fürs Interesse an brain-indexer! Kurze Regeln, damit das Repo
klein, deterministisch und sicher bleibt.

## Konventionen

- **Python stdlib only.** Keine Drittabhängigkeiten — weder Laufzeit noch
  Build. Wenn ein Feature eine Abhängigkeit bräuchte, gehört es hinter eine
  Option (wie `qmd` für die semantische Suche).
- **Determinismus.** Gleicher Vault → gleiches Ergebnis: keine Zufalls-
  oder Zeitanteile im Index, stabile Sortierungen, reproduzierbare
  Communities. Fixes dürfen bestehende Ausgaben nur gezielt ändern.
- **Dokumentation deutsch, Code-Kommentare englisch/deutsch knapp.**
- **Keine Credentials oder persönlichen Daten** im Code, in Tests, in
  Beispielen. Konfiguration läuft über Umgebungsvariablen bzw.
  `brain.env` (gitignored, siehe `.env.example`).

## Entwickeln & Testen

```bash
python3 -m py_compile indexer.py server.py link_report.py make_synth_vault.py
python3 test_indexer.py          # muss "ALLE TESTS GRÜN" drucken
python3 make_synth_vault.py /tmp/synthvault 500   # Testvault
python3 indexer.py /tmp/synthvault                # Smoke-Test
```

Nach dem Klonen einmalig die Git-Hooks aktivieren (Syntax- + Test-Check
vor jedem Push):

```bash
bash scripts/setup-hooks.sh
```

## Release-Flow

1. Im privaten Repo entwickeln und committen.
2. `bash export-public.sh` — erzeugt/aktualisiert den sauberen
   Public-Export (frische Historie, Ausschlüsse siehe Script).
3. `bash scripts/release-gates.sh ../brain-indexer-public` — alle Gates
   müssen GRÜN sein, sonst kein Push. `--push` pusht automatisch.
4. Push nach `https://github.com/SiLenTTz/brain-indexer.git`.

## Lizenz

MIT — Beiträge erfolgen unter derselben Lizenz.
