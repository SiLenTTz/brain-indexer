# brain-indexer

The deterministic backend for [brain-galaxy](https://github.com/SiLenTTz/brain-galaxy):
turns a markdown vault into a SQLite graph index — nodes, resolved links,
folder levels, topic communities — and serves it over localhost with a
level-of-detail API, note editing (with git commits), live updates (SSE)
and optional semantic search. **Python stdlib only** (SQLite + FTS5), no
network required, no AI in the indexing path.

## Voraussetzungen

- Python ≥ 3.10 — **stdlib only** (keine Abhängigkeiten, kein pip install)
- SQLite ist inklusive; FTS5 ist in den meisten Python-Builds enthalten
  (fehlendes FTS5 ist kein Fehler — die Suche fällt dann einfach weg)
- Optional: `qmd` für semantische Suche (`/agent`, `/similar`) und
  `LIBRARIAN_URL`/`LIBRARIAN_TOKEN` für den Remote-Librarian (`/hive`)

## Quick start

```bash
python3 indexer.py ~/YourVault     # → YourVault/.brain/index.db + graph.json
python3 server.py                  # → http://127.0.0.1:8789
python3 test_indexer.py            # fixture tests (+ live tests if a vault exists)
```

Point brain-galaxy at it (it proxies to :8789 by default) and your vault
appears as a universe. Obsidian and plain-markdown folders work as-is;
other data sources: see the [import contract](https://github.com/SiLenTTz/brain-galaxy/blob/main/docs/IMPORT.md)
(Source Graph JSON v1, adapters, importer agent).

**Deutsch / Kurz:** `python3 indexer.py ~/Vault && python3 server.py` —
fertig. Der Indexer ist deterministisch (gleicher Vault → gleiches Ergebnis,
keine KI), Markdown bleibt die einzige Wahrheit, alles Abgeleitete ist
wegwerfbar und neu baubar.

## What it builds

| Artifact | Content |
| --- | --- |
| `index.db` (SQLite, WAL, schema v2) | `nodes` · `links_raw` (resolution status per raw link) · `edges` · `folders` (aggregate levels) · `communities` (label propagation over wikilinks, deterministic) · `nodes_fts` (FTS5) |
| `graph.json` | compatibility export for existing consumers |

Link resolution follows Obsidian semantics: exact path → unique file name →
same-folder preference → unresolved **with reason** (`ambiguous`/`missing`).
`link_report.py` writes a curation report of unresolved links into your
vault inbox.

## HTTP API (:8789)

| Endpoint | Purpose |
| --- | --- |
| `/api/v1/graph?folder=\|community=&limit=` | Level-of-detail views (level 0 = folder super-nodes + inter-folder link flows) |
| `/api/v1/graph/meta` | counts, clusters, folder tree, communities |
| `/api/v1/events` | SSE: `graph-updated` after reindex |
| `/graph.json` | full export (compat) |
| `/note` GET/POST | read / write notes (POST commits to git, triggers reindex) |
| `/agent`, `/similar`, `/timeline`, `/hive` | optional: semantic search (qmd), related notes, activity, remote librarian proxy |

## Configuration (per vault)

- **`<vault>/.brain/clusters.json`** — maps top-level folders to clusters:
  `{"00_inbox": ["System", "#6b7280"], ...}`. If present, it **replaces**
  the built-in example mapping entirely; unreadable JSON falls back to the
  defaults with a warning on stderr. Env `BRAIN_CLUSTERS=/path/to.json`
  overrides the location.
- **`<vault>/.brain/live-check.json`** — JSON array of note paths that must
  exist in the index; its presence also raises the validation thresholds
  (40+ nodes, 3+ wikilinks). Without the file, the live-golden check
  self-skips (exit code stays green) — CI-safe.

## Operation

```bash
BRAIN_VAULT=/path BRAIN_PORT=8790 BRAIN_CORS=1 python3 server.py  # second instance
cp .env.example brain.env   # optional: LIBRARIAN_* for /hive
```

A watcher thread reindexes on change (~10 s debounce); run via systemd for
production (`brain-indexer.service`, `brain-reindex.timer` nightly safety
net — example units in [`systemd/`](systemd/)):

```bash
mkdir -p ~/.config/systemd/user
cp systemd/brain-indexer.service systemd/brain-reindex.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now brain-indexer.service brain-reindex.timer
```

Environment overrides: `BRAIN_VAULT`, `BRAIN_PORT`, `BRAIN_CORS`,
`BRAIN_CLUSTERS`, `LIBRARIAN_URL`, `LIBRARIAN_TOKEN`.

## Tools

- `make_synth_vault.py <dir> <n>` — deterministic synthetic vault (e.g.
  10 000 notes) for benchmarks
- `link_report.py [vault]` — unresolved-link curation report → vault inbox
- `indexer.py <vault>` — rebuild index anytime; derived artifacts are
  disposable by design

## License

MIT. Your data stays yours: everything runs locally, indexes are derived
and deletable, writes go through your own git.
