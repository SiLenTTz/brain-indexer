# Changelog

## [1.0.0] - 2026-09-24
Initial public release.

- Deterministischer Markdown-Indexer mit Obsidian-kompatibler
  Wikilink-Auflösung (Pfad → Stem → Same-Folder → unresolved mit Grund)
- SQLite-Index (WAL, schema v2) + optionale FTS5-Suche
- LOD-Graph-API: Ordner- und Community-Sichten, Ebenen-Aggregate
- SSE-Live-Updates (`graph-updated`) + Vault-Watcher mit Debounce
- Synth-Vault-Generator für Skalierungs-Benchmarks
