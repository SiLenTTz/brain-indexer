#!/usr/bin/env python3
"""Kuratierungs-Report für mehrdeutige/fehlende Wikilinks (S1-Follow-up).

Liest links_raw aus dem SQLite-Index und schreibt einen Markdown-Report
in den Vault (00_inbox — Kuratoren-Regel). Aufruf:
    python3 link_report.py [vault]     (Default ~/Vault)
"""
import pathlib
import sqlite3
import sys
from collections import defaultdict
from datetime import date

VAULT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path.home() / "Vault"))
DB = VAULT / ".brain" / "index.db"
OUT = VAULT / "00_inbox" / f"WIKILINK-KURATIERUNG-{date.today().isoformat()}.md"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

# ambiguous: mehrere Kandidaten mit gleichem Stem
stems = defaultdict(list)
for r in con.execute("SELECT id FROM nodes"):
    stems[pathlib.PurePosixPath(r["id"]).stem].append(r["id"])

amb = defaultdict(list)   # raw -> [(quelle, ziel-kandidaten)]
missing = defaultdict(list)
for r in con.execute(
        "SELECT source, raw FROM links_raw WHERE resolution IN ('ambiguous','missing')"):
    stem = pathlib.PurePosixPath(r["raw"]).stem
    cands = stems.get(stem, [])
    if r["raw"] not in amb and r["raw"] not in missing:
        pass
    if len(cands) > 1:
        amb[r["raw"]].append((r["source"], cands))
    else:
        missing[r["raw"]].append(r["source"])

lines = [
    "---",
    f"title: Wikilink-Kuratierte — {date.today().isoformat()}",
    "type: kuratierung",
    "---",
    "",
    f"# Wikilink-Kuratierte ({date.today().isoformat()})",
    "",
    "Aus dem Graph-Index (schema v2, Obsidian-Auflösung): Links, die **keine",
    "eindeutige Kante** erzeugen. Mehrdeutige = mehrere Dateien mit gleichem",
    "Namen; Fehlende = Ziel existiert nicht. Fix-Optionen: Ziel eindeutig",
    "verlinken (`[[ordner/name]]`), Datei umbenennen, oder Link aufräumen.",
    "",
    f"- **{sum(len(v) for v in amb.values())} mehrdeutige Link-Ziele** (Stem mehrfach vorhanden)",
    f"- **{sum(len(v) for v in missing.values())} tote Link-Ziele** (Ziel fehlt)",
    "",
]

lines.append("## Mehrdeutig (Stem existiert mehrfach)\n")
for raw in sorted(amb, key=lambda k: -len(amb[k])):
    quellen, _ = amb[raw][0]
    cands = amb[raw][0][1]
    lines.append(f"### `[[{raw}]]` ({len(amb[raw])} Verweis/e)")
    lines.append(f"- Kandidaten: {', '.join(f'`{c}`' for c in sorted(cands))}")
    for quelle, _ in amb[raw][:5]:
        lines.append(f"- verlinkt von: `{quelle}`")
    if len(amb[raw]) > 5:
        lines.append(f"- … und {len(amb[raw]) - 5} weitere")
    lines.append("")

lines.append("## Fehlende Ziele (tote Links)\n")
for raw in sorted(missing, key=lambda k: -len(missing[k])):
    lines.append(f"### `[[{raw}]]` ({len(missing[raw])}×)")
    for quelle in missing[raw][:4]:
        lines.append(f"- `{quelle}`")
    if len(missing[raw]) > 4:
        lines.append(f"- … und {len(missing[raw]) - 4} weitere")
    lines.append("")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"{OUT} ({OUT.stat().st_size // 1024} KB): "
      f"{sum(len(v) for v in amb.values())} ambiguous, "
      f"{sum(len(v) for v in missing.values())} missing")
