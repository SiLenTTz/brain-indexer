# AGENTS.md

Kurzfassung für Coding-Agents (und Menschen) in diesem Repo.

## Zweck

brain-indexer ist das deterministische Backend für brain-galaxy: baut aus
einem Markdown-Vault einen SQLite-Graphindex (`<vault>/.brain/index.db` +
`graph.json`) und stellt ihn per HTTP bereit. Markdown ist die einzige
Wahrheit; alles Abgeleitete ist wegwerfbar und neu baubar.

## Konventionen

- **Python stdlib only** — keine Drittabhängigkeiten.
- **Determinismus**: gleicher Vault → gleiches Ergebnis (kein Zufall, keine
  Zeitanteile im Index, stabile Sortierungen).
- Doku deutsch, Code-Kommentare knapp.
- Keine Credentials/persönlichen Daten im Repo (Release-Gates prüfen das).

## Befehle

```bash
python3 indexer.py <vault>       # Index bauen (Default ~/Vault)
python3 server.py                # API auf http://127.0.0.1:8789
python3 test_indexer.py          # Tests (müssen grün sein)
bash scripts/release-gates.sh <export-dir>   # Release-Gates
```

## Release-Gates-Regel

Vor jedem Public-Push: `bash scripts/release-gates.sh ../brain-indexer-public`
muss "ALLE GATES GRÜN" melden. Rotes Gate = kein Push, erst beheben.

## Karten der Dateien

| Datei | Rolle |
| --- | --- |
| `indexer.py` | Vault → SQLite + graph.json (deterministisch) |
| `server.py` | HTTP-API, SSE, Watcher, Notiz-Edit via git |
| `test_indexer.py` | Fixture- + Live-Golden-Tests |
| `link_report.py` | Kuratierungs-Report für ungelöste Wikilinks |
| `make_synth_vault.py` | deterministischer Synth-Vault für Benches |
| `export-public.sh` | sauberer Public-Export aus dem privaten Repo |
| `scripts/release-gates.sh` | Release-Gates (Audit, Build, Tests, …) |
| `systemd/` | Beispiel-Units für Dauerbetrieb |
