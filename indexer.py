#!/usr/bin/env python3
"""brain-indexer v2 — deterministischer Vault-Indexer (2026-09).

Deterministisches Skript OHNE KI. Regeln (aus der Bauanleitung):
1. Markdown-Dateien sind die einzige Wahrheit (Human-SSOT, Menschen lesen .md).
2. Die Landkarte (.brain/index.db + graph.json) ist ein abgeleiteter
   Zwischenstand — bei jedem Lauf neu erzeugt, nie von Hand gepflegt.
3. Auslesen ist strikt getrennt vom Anzeigen.

v2 (analysis/adr/ADR-002 rev.):
- SQLite als Maschinenindex (index.db, WAL, schema_version) + graph.json
  als kompatibler Export für die laufende Galaxy.
- Wikilink-Auflösung wie Obsidian: exakter Pfad, sonst eindeutiger Stem,
  mehrdeutig -> unresolved (nicht mehr Alle-Treffer — das blähte 1 Link
  zu 336 Kanten auf).
- Dot-Ordner (.stversions, .opencode, .claude, ...) werden ignoriert.
- Ebenen-Aggregate (folders) + Communities (deterministische Label-
  Propagation über Wikilinks) als Grundlage für LOD/Drill-down (ADR-003).

Aufruf: python3 indexer.py [vault-pfad]   (Default $HOME/Vault)
"""

import json
import os
import pathlib
import re
import sys
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

SCHEMA_VERSION = 2

IGNORE_DIRS = {".obsidian", ".trash", ".git", ".brain", "08_Attachments",
               # E-Mail-Archive sind Archiv, kein Wissen (null Wikilinks) —
               # sie gehören nicht in die Wissens-Galaxie.
               "emails"}

CLUSTERS = {
    # Neutrale Beispiel-Ordner (passend zu make_synth_vault.py). Wird komplett
    # ersetzt durch <vault>/.brain/clusters.json bzw. $BRAIN_CLUSTERS —
    # siehe load_clusters() und README.
    "00_inbox": ("System", "#6b7280"),
    "10_archive": ("Archive", "#6b7280"),
    "20_systems": ("System", "#6b7280"),
    "30_topics": ("Topics", "#a855f7"),
    "40_projects": ("Projects", "#4f8cff"),
    "50_jobs": ("Work", "#f59e0b"),
    "90_archive": ("Archive", "#6b7280"),
    "99_templates": ("System", "#6b7280"),
    "docs": ("Topics", "#a855f7"),
    "emails": ("Archive", "#6b7280"),
}
DEFAULT_CLUSTER = ("System", "#6b7280")


def load_clusters(vault: pathlib.Path) -> None:
    """CLUSTERS aus <vault>/.brain/clusters.json (oder $BRAIN_CLUSTERS)
    laden; ersetzt die Defaults komplett. Format:
    {"<ordner>": ["<Cluster>", "<#farbe>"], ...}
    Bei Fehlern: Defaults behalten, Warnung auf stderr."""
    global CLUSTERS
    env = os.environ.get("BRAIN_CLUSTERS")
    path = pathlib.Path(env) if env else vault / ".brain" / "clusters.json"
    if not path.exists():
        if env:
            print(f"WARNUNG: BRAIN_CLUSTERS={env} nicht gefunden — "
                  f"nutze Standard-CLUSTERS", file=sys.stderr)
        return
    try:
        data = json.loads(path.read_text())
        loaded = {str(k): (str(v[0]), str(v[1])) for k, v in data.items()}
        if not loaded:
            raise ValueError("leere Cluster-Map")
        CLUSTERS = loaded
    except Exception as e:
        print(f"WARNUNG: {path} unlesbar ({e}) — nutze Standard-CLUSTERS",
              file=sys.stderr)

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
TAG = re.compile(r"(?<!\S)#([a-zA-ZäöüÄÖÜß][\w/-]*)")
H1 = re.compile(r"^#\s+(.+)$", re.M)

FOLDER_NEIGHBORS = 8          # Kap gegen Kanten-Explosion in großen Ordnern
COMMUNITY_MAX_ROUNDS = 25


def cluster_for(rel: str):
    top = rel.split("/", 1)[0]
    return CLUSTERS.get(top, DEFAULT_CLUSTER)


def ignored(rel_path: pathlib.Path) -> bool:
    """IGNORE_DIRS + alle Dot-Ordner (Syncthing-/Tool-Begleitdateien)."""
    for part in rel_path.parts:
        if part in IGNORE_DIRS or part.startswith("."):
            return True
    return False


def collect_nodes(vault: pathlib.Path):
    nodes, by_stem = [], defaultdict(list)
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault)
        if ignored(rel):
            continue
        text = f.read_text(errors="replace")
        m = H1.search(text)
        title = m.group(1).strip() if m else f.stem
        links = sorted(set(x.strip() for x in WIKILINK.findall(text)))
        tags = sorted(set(t for t in TAG.findall(text) if not t.isdigit()))
        cname, ccolor = cluster_for(str(rel))
        nodes.append({
            "id": str(rel),
            "title": title,
            "path": str(rel),
            "cluster": cname,
            "color": ccolor,
            "tags": tags,
            "links_raw": links,
            "words": len(text.split()),
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
        })
        by_stem[f.stem].append(str(rel))
    return nodes, by_stem


def resolve_link(raw: str, source_id: str, by_path: dict, by_stem: dict):
    """Obsidian-Regel: exakter Pfad, sonst eindeutiger Stem — sonst unresolved.
    Zusatz (Kuratierung 2026-09-10): Same-Folder-Präferenz — Tagesnotizen
    verlinken [[prose]]/[[index]] und meinen die Datei DESSELBEN Tages; das
    löst ~200 der 298 Mehrdeutigkeiten sauber auf."""
    cand = raw if raw.endswith(".md") else raw + ".md"
    if cand in by_path:
        return cand, "path"
    stem = pathlib.PurePosixPath(raw).stem
    hits = by_stem.get(stem, [])
    src_dir = str(pathlib.PurePosixPath(source_id).parent)
    same = [h for h in hits if str(pathlib.PurePosixPath(h).parent) == src_dir]
    if len(same) == 1:
        return same[0], "stem"
    if len(hits) == 1:
        return hits[0], "stem"
    if len(hits) > 1:
        return None, "ambiguous"
    return None, "missing"


def build_edges(nodes, by_stem):
    """Wikilink-Kanten (aufgelöst wie Obsidian) + Ordner-Nachbarschaft."""
    by_path = {n["id"]: n for n in nodes}
    edges, unresolved, link_rows = [], [], []
    for n in nodes:
        for raw in n["links_raw"]:
            target, how = resolve_link(raw, n["id"], by_path, by_stem)
            link_rows.append({"source": n["id"], "raw": raw,
                              "resolution": how, "target": target})
            if target is None or target == n["id"]:
                if target is None:
                    unresolved.append({"from": n["id"], "raw": raw, "reason": how})
                continue
            edges.append({"source": n["id"], "target": target, "type": "wikilink"})

    # Ordner-Nachbarschaft — nur für Themen/Quellen-Sicht; CAP gegen
    # vollständige Paare in großen Ordnern (quadratisch viele Kanten).
    by_dir = defaultdict(list)
    for n in nodes:
        by_dir[str(pathlib.PurePosixPath(n["id"]).parent)].append(n["id"])
    for d, ids in by_dir.items():
        if len(ids) <= 1:
            continue
        for i, a in enumerate(ids):
            for j in range(1, FOLDER_NEIGHBORS + 1):
                b = ids[(i + j) % len(ids)]
                if i < ids.index(b):
                    edges.append({"source": a, "target": b, "type": "folder"})
    return edges, unresolved, link_rows


def detect_communities(nodes, wikilink_edges):
    """Deterministische Label-Propagation über den Wikilink-Graph.

    Async-Iteration über sortierte IDs; Labels wandern nur abwärts
    (min-Tiebreak) -> terminiert immer, Ergebnis reproduzierbar.
    """
    adj = defaultdict(set)
    for e in wikilink_edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    labels = {n["id"]: n["id"] for n in nodes}
    order = sorted(labels)
    for _ in range(COMMUNITY_MAX_ROUNDS):
        changed = False
        for nid in order:
            counts = Counter(labels[t] for t in adj.get(nid, ()) if t in labels)
            if not counts:
                continue
            best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if best < labels[nid]:
                labels[nid] = best
                changed = True
        if not changed:
            break

    groups = defaultdict(list)
    for nid, lab in labels.items():
        groups[lab].append(nid)
    # stabil benennen: c1, c2, ... nach Größe (Abst), dann Label; Top-Titel mitgeben
    by_node = {n["id"]: n for n in nodes}
    degree = Counter()
    for e in wikilink_edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    comm_of, comm_meta = {}, []
    for i, (lab, members) in enumerate(ordered, start=1):
        cid = f"c{i}"
        for m in members:
            comm_of[m] = cid
        top = sorted(members, key=lambda m: (-degree.get(m, 0), m))[0]
        comm_meta.append({
            "id": cid,
            "label": by_node[top]["title"][:60],
            "anchor": top,
            "nodes": len(members),
            "words": sum(by_node[m]["words"] for m in members),
            "wikilinks": sum(degree.get(m, 0) for m in members) // 2,
        })
    return comm_of, comm_meta


def build_folders(nodes):
    """Ebenen-Aggregate: jeder Ordner im Baum mit Node-/Wort-Summen (LOD-0)."""
    agg = defaultdict(lambda: {"nodes": 0, "words": 0})
    for n in nodes:
        parts = pathlib.PurePosixPath(n["id"]).parts[:-1]
        for d in range(1, len(parts) + 1):
            agg["/".join(parts[:d])]["nodes"] += 1
            agg["/".join(parts[:d])]["words"] += n["words"]
    out = []
    for path, v in sorted(agg.items()):
        parent = "/".join(path.split("/")[:-1])
        out.append({"path": path, "parent": parent or None,
                    "depth": len(path.split("/")), **v})
    return out


def write_sqlite(out_dir: pathlib.Path, nodes, edges, link_rows,
                 folders, communities, comm_of, meta):
    db = out_dir / "index.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE nodes (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, path TEXT NOT NULL,
      cluster TEXT NOT NULL, color TEXT NOT NULL, tags TEXT NOT NULL,
      words INTEGER NOT NULL, size INTEGER NOT NULL, mtime TEXT NOT NULL,
      community TEXT NOT NULL, degree INTEGER NOT NULL);
    CREATE INDEX idx_nodes_cluster ON nodes(cluster);
    CREATE INDEX idx_nodes_community ON nodes(community);
    CREATE TABLE links_raw (
      source TEXT NOT NULL, raw TEXT NOT NULL,
      resolution TEXT NOT NULL CHECK (resolution IN ('path','stem','ambiguous','missing')),
      target TEXT);
    CREATE INDEX idx_links_source ON links_raw(source);
    CREATE TABLE edges (
      source TEXT NOT NULL, target TEXT NOT NULL, type TEXT NOT NULL,
      PRIMARY KEY (source, target, type));
    CREATE INDEX idx_edges_target ON edges(target);
    CREATE TABLE folders (
      path TEXT PRIMARY KEY, parent TEXT, depth INTEGER NOT NULL,
      nodes INTEGER NOT NULL, words INTEGER NOT NULL);
    CREATE TABLE communities (
      id TEXT PRIMARY KEY, label TEXT NOT NULL, anchor TEXT NOT NULL,
      nodes INTEGER NOT NULL, words INTEGER NOT NULL, wikilinks INTEGER NOT NULL);
    """)
    degree = Counter()
    for e in edges:
        if e["type"] == "wikilink":
            degree[e["source"]] += 1
            degree[e["target"]] += 1
    con.executemany("INSERT INTO meta VALUES (?,?)",
                    [(k, str(v)) for k, v in meta.items()])
    con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(n["id"], n["title"], n["path"], n["cluster"], n["color"],
                      ",".join(n["tags"]), n["words"], n["size"], n["mtime"],
                      comm_of[n["id"]], degree.get(n["id"], 0)) for n in nodes])
    con.executemany("INSERT INTO links_raw VALUES (?,?,?,?)",
                    [(r["source"], r["raw"], r["resolution"], r["target"])
                     for r in link_rows])
    con.executemany("INSERT OR IGNORE INTO edges VALUES (?,?,?)",
                    [(e["source"], e["target"], e["type"]) for e in edges])
    con.executemany("INSERT INTO folders VALUES (?,?,?,?,?)",
                    [(f["path"], f["parent"], f["depth"], f["nodes"], f["words"])
                     for f in folders])
    con.executemany("INSERT INTO communities VALUES (?,?,?,?,?,?)",
                    [(c["id"], c["label"], c["anchor"], c["nodes"],
                      c["words"], c["wikilinks"]) for c in communities])
    # FTS5 nur wenn vorhanden (Suche läuft primär über qmd)
    try:
        con.execute("CREATE VIRTUAL TABLE nodes_fts USING fts5(title, path)")
        con.executemany("INSERT INTO nodes_fts VALUES (?,?)",
                        [(n["title"], n["path"]) for n in nodes])
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()
    return db


def main(vault_arg=None):
    if vault_arg is None and len(sys.argv) > 1:
        vault_arg = sys.argv[1]
    vault = pathlib.Path(vault_arg or os.path.join(pathlib.Path.home(), "Vault"))
    out_dir = vault / ".brain"
    out_dir.mkdir(exist_ok=True)
    load_clusters(vault)

    nodes, by_stem = collect_nodes(vault)
    edges, unresolved, link_rows = build_edges(nodes, by_stem)
    wikilink_edges = [e for e in edges if e["type"] == "wikilink"]
    comm_of, communities = detect_communities(nodes, wikilink_edges)
    folders = build_folders(nodes)
    for n in nodes:
        n["community"] = comm_of[n["id"]]

    cluster_stats = defaultdict(lambda: {"nodes": 0, "words": 0})
    for n in nodes:
        cluster_stats[n["cluster"]]["nodes"] += 1
        cluster_stats[n["cluster"]]["words"] += n["words"]

    graph = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "vault": str(vault),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "clusters": [
            {"name": k, "color": v, "nodes": cluster_stats[k]["nodes"],
             "words": cluster_stats[k]["words"]}
            for (k, v) in sorted({c: col for c, col in CLUSTERS.values()}.items())
            if cluster_stats.get(k)
        ],
        "communities": communities,
        "folders": folders,
        "unresolved_links": unresolved,
        "nodes": nodes,
        "edges": edges,
    }
    (out_dir / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1))

    meta = {"schema_version": SCHEMA_VERSION,
            "generated_at": graph["generated_at"], "vault": str(vault),
            "node_count": len(nodes), "edge_count": len(edges)}
    db = write_sqlite(out_dir, nodes, edges, link_rows, folders,
                      communities, comm_of, meta)

    # --- Validierung (Live-Golden-Pfade aus <vault>/.brain/live-check.json) ---
    node_ids = {n["id"] for n in nodes}
    live_check = vault / ".brain" / "live-check.json"
    expected = []
    if live_check.exists():
        try:
            expected = [str(p) for p in json.loads(live_check.read_text())]
        except Exception as e:
            print(f"WARNUNG: {live_check} unlesbar ({e}) — Live-Check übersprungen",
                  file=sys.stderr)
            expected = []
    is_live = str(vault) == str(pathlib.Path(pathlib.Path.home(), "Vault"))
    if is_live and not expected:
        print("Hinweis: keine live-check.json im Vault — Live-Golden-Check übersprungen")
    min_nodes = 40 if expected else 3
    min_wikilinks = 3 if expected else 1
    missing = [e for e in expected if e not in node_ids]
    dot_leak = [i for i in node_ids if any(p.startswith(".") for p in pathlib.PurePosixPath(i).parts[:-1])]
    res_counts = Counter(r["resolution"] for r in link_rows)
    ok = (not missing and not dot_leak and len(wikilink_edges) >= min_wikilinks
          and len(nodes) >= min_nodes and set(comm_of) == node_ids)

    print(f"Nodes: {len(nodes)}  Edges: {len(edges)} "
          f"(wikilink: {len(wikilink_edges)}, folder: {len(edges)-len(wikilink_edges)})")
    print(f"Links: {dict(res_counts)}  Unresolved: {len(unresolved)}")
    print(f"Communities: {len(communities)}  Folders: {len(folders)}")
    for c in communities[:8]:
        print(f"  {c['id']:<4} {c['nodes']:>3} Nodes  {c['label']}")
    print(f"DB: {db} ({db.stat().st_size//1024} KB)  "
          f"graph.json: {(out_dir/'graph.json').stat().st_size//1024} KB")
    print(f"VALIDATION: {'PASS' if ok else 'FAIL'}")
    if missing:
        print(f"  fehlend: {missing}")
    if dot_leak:
        print(f"  Dot-Pfad indiziert: {dot_leak[:5]}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
