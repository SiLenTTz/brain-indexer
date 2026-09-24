#!/usr/bin/env python3
"""Tests für den brain-indexer v2 (S1/S2, analysis/adr/ADR-002 rev.).

Zwei Ebenen:
- Fixture-Tests (deterministisch, ohne Live-Vault): Bau einer Mini-Vault im
  tmp-Verzeichnis, Indexer-Lauf, Assertions gegen graph.json UND index.db.
- Live-Golden-Tests (übersprungen, wenn ~/Vault/.brain/graph.json ODER
  live-check.json fehlt): erwartete Notizen aus live-check.json, Invarianten
  der Link-Auflösung, keine Dot-Pfade (CI-sicher ohne Live-Vault).
"""
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
INDEXER = HERE / "indexer.py"
LIVE_GRAPH = pathlib.Path.home() / "Vault/.brain/graph.json"
LIVE_DB = pathlib.Path.home() / "Vault/.brain/index.db"
LIVE_CHECK = pathlib.Path.home() / "Vault/.brain/live-check.json"


def run_indexer(vault: pathlib.Path) -> dict:
    r = subprocess.run([sys.executable, str(INDEXER), str(vault)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"indexer failed: {r.stderr}"
    return json.loads((vault / ".brain" / "graph.json").read_text())


# ---------- Fixture ----------

def make_fixture(root: pathlib.Path):
    (root / "docs").mkdir()
    (root / "docs" / "sub").mkdir()
    (root / "other").mkdir()
    (root / ".stversions").mkdir()
    (root / "docs" / "a.md").write_text(
        "# Alpha\nSiehe [[b]] und [[Charlie]] und [[x]] und [[fehlt]].\n")
    (root / "docs" / "d.md").write_text("# Delta\n[[x]] meint die eigene Ordner-Datei.\n")
    (root / "docs" / "sub" / "b.md").write_text("# Bravo\nzurück zu [[a]].\n")
    (root / "other" / "x.md").write_text("# X eins\n")
    (root / "docs" / "x.md").write_text("# X zwei\n")
    (root / "docs" / "c.md").write_text("# Charlie\n[[docs/sub/b]] exakt.\n")
    (root / ".stversions" / "old.md").write_text("# soll nicht da sein\n")


def test_fixture_resolution():
    with tempfile.TemporaryDirectory() as td:
        vault = pathlib.Path(td)
        make_fixture(vault)
        g = run_indexer(vault)
        ids = {n["id"] for n in g["nodes"]}
        assert ids == {"docs/a.md", "docs/sub/b.md", "docs/x.md",
                       "other/x.md", "docs/c.md", "docs/d.md"}, f"ids: {ids}"
        wl = {(e["source"], e["target"]) for e in g["edges"] if e["type"] == "wikilink"}
        assert ("docs/a.md", "docs/sub/b.md") in wl, "stem-Auflösung a→b"
        assert ("docs/c.md", "docs/sub/b.md") in wl, "Pfad-Auflösung c→b"
        assert ("docs/sub/b.md", "docs/a.md") in wl, "stem-Auflösung b→a"
        # Same-Folder-Präferenz: d verlinkt [[x]] → docs/x.md (nicht ambiguous)
        assert ("docs/d.md", "docs/x.md") in wl, "Same-Folder-Präferenz d→x"
        # a (Ordner docs!) verlinkt [[x]] → ebenfalls Same-Folder → docs/x.md
        assert ("docs/a.md", "docs/x.md") in wl, "Same-Folder-Präferenz a→x"
        assert not any(s == "docs/d.md" and t == "other/x.md" for s, t in wl)
        reasons = {u["raw"]: u["reason"] for u in g["unresolved_links"]}
        assert reasons.get("fehlt") == "missing", reasons
        assert "x" not in reasons, "x ist jetzt same-folder eindeutig"
        assert ".stversions/old.md" not in ids, "Dot-Ordner ignoriert"


def test_fixture_schema_communities_db():
    with tempfile.TemporaryDirectory() as td:
        vault = pathlib.Path(td)
        make_fixture(vault)
        g = run_indexer(vault)
        assert g["schema_version"] == 2
        # a↔b verbindet → gleiche Community; c→b gehört dazu; x seit
        # Same-Folder-Präferenz ebenfalls angebunden (a→x, d→x)
        comm = {n["id"]: n["community"] for n in g["nodes"]}
        assert comm["docs/a.md"] == comm["docs/sub/b.md"] == comm["docs/c.md"]
        assert comm["docs/x.md"] == comm["docs/a.md"]
        assert comm["other/x.md"] != comm["docs/a.md"]
        # SQLite: Tabellen, Zeilenzahlen, resolution-Status, FTS
        db = sqlite3.connect(vault / ".brain" / "index.db")
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
        assert db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 6
        assert db.execute("SELECT COUNT(*) FROM nodes_fts").fetchone()[0] == 6
        rows = dict(db.execute(
            "SELECT raw, resolution FROM links_raw WHERE source='docs/a.md'").fetchall())
        # [[Charlie]] bleibt missing: Obsidian löst über Dateinamen, nicht Titel;
        # [[x]] ist same-folder eindeutig → stem
        assert rows == {"b": "stem", "Charlie": "missing",
                        "x": "stem", "fehlt": "missing"}, rows
        deg = dict(db.execute("SELECT id, degree FROM nodes").fetchall())
        assert deg["docs/a.md"] >= 1 and deg["docs/sub/b.md"] >= 2
        # folders-Aggregat
        f = {r[0]: r[1] for r in db.execute("SELECT path, nodes FROM folders").fetchall()}
        assert f.get("docs") == 5 and f.get("docs/sub") == 1, f


def test_api_v1_lod():
    """LOD-API (S6) gegen die Fixture-DB: Ebene 0, Ordner- und Community-Sicht."""
    import importlib.util
    with tempfile.TemporaryDirectory() as td:
        vault = pathlib.Path(td)
        make_fixture(vault)
        run_indexer(vault)
        spec = importlib.util.spec_from_file_location("srv", HERE / "server.py")
        srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(srv)
        srv.VAULT = vault
        srv.GRAPH = vault / ".brain" / "graph.json"
        srv.DB = vault / ".brain" / "index.db"

        meta = srv.api_graph_meta()
        assert meta["schema_version"] == 2
        assert {f["path"] for f in meta["folders"]} == {"docs", "docs/sub", "other"}

        lvl0 = srv.api_graph()
        ids0 = {n["id"] for n in lvl0["nodes"]}
        assert ids0 == {"folder:docs", "folder:other"}, ids0
        # Ebene-0-Kante docs↔other existiert (a.md→other/x.md wird ambiguous —
        # c.md→docs/sub/b.md bleibt innerhalb docs; docs↔other entsteht nicht
        # zwingend → nur Struktur prüfen):
        assert all(e["source"].startswith("folder:") for e in lvl0["edges"])

        sub = srv.api_graph(folder="docs")
        ids = {n["id"] for n in sub["nodes"]}
        assert "folder:docs/sub" in ids and "docs/a.md" in ids
        assert not any(i.startswith("other") for i in ids)
        for e in sub["edges"]:
            assert e["source"] in ids and e["target"] in ids

        comm_id = srv.api_graph_meta()["communities"][0]["id"]
        com = srv.api_graph(community=comm_id)
        assert all(n["community"] == comm_id for n in com["nodes"])


# ---------- Live-Golden (nur wenn der Live-Vault indexiert ist) ----------

def test_live_golden():
    if not (LIVE_GRAPH.exists() and LIVE_CHECK.exists()):
        print("SKIP test_live_golden (kein Live-Graph oder keine live-check.json)")
        return
    g = json.loads(LIVE_GRAPH.read_text())
    assert g["schema_version"] == 2
    ids = {n["id"] for n in g["nodes"]}
    for e in json.loads(LIVE_CHECK.read_text()):
        assert e in ids, f"fehlt: {e}"
    assert g["node_count"] >= 40
    wl = [e for e in g["edges"] if e["type"] == "wikilink"]
    assert len(wl) >= 3
    # keine Kante in Dot-Ordner; alle Kanten-Enden existieren
    assert not any(p.startswith(".") for n in ids for p in n.split("/")[:-1])
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    # Communities überdecken alle Nodes
    comm = {n["community"] for n in g["nodes"]}
    assert all(c for c in comm)
    if LIVE_DB.exists():
        db = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        assert db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == g["node_count"]


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("ALLE TESTS GRÜN")
