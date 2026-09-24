#!/usr/bin/env python3
"""Lokaler Webserver fuer die Brain-Galaxy-UI (EPIC-69 P3-T3, v4).

Endpunkte: statisch (index.html, vendor/), /graph.json (Vault .brain/),
/note GET+POST (Modal lesen/editieren mit Git-Commit),
/agent?q= (Chat: qmd-Bedeutungssuche, liefert Antwort + Best-Treffer).
"""
import http.server
import json
import os
import pathlib
import sqlite3
import subprocess
import socketserver
import sys
import threading
import time
import urllib.parse
import functools
import re
import html as html_mod

HERE = pathlib.Path(__file__).parent
# BRAIN_VAULT/BRAIN_PORT überschreibbar für Bench-Zweitinstanzen (S10)
VAULT = pathlib.Path(os.environ.get("BRAIN_VAULT", str(pathlib.Path.home() / "Vault")))
GRAPH = VAULT / ".brain" / "graph.json"
DB = VAULT / ".brain" / "index.db"
PORT = int(os.environ.get("BRAIN_PORT", "8789"))
# Librarian (optionales Remote-Brain) — Credentials aus der Umwelt (systemd
# EnvironmentFile=brain.env, siehe .env.example); NICHT mehr im Code.
LIBRARIAN_URL = os.environ.get("LIBRARIAN_URL", "")
LIBRARIAN_TOKEN = os.environ.get("LIBRARIAN_TOKEN", "")


def vault_rel(rel: str) -> pathlib.Path:
    p = (VAULT / rel).resolve()
    if not str(p).startswith(str(VAULT.resolve())):
        raise ValueError("Pfad ausserhalb des Vaults")
    if p.suffix != ".md":
        raise ValueError("nur .md-Dateien")
    return p


def git_commit(path: pathlib.Path, message: str) -> str:
    # Identität: die git-config des Nutzers; optional per env überschreibbar
    # (BRAIN_GIT_NAME/BRAIN_GIT_EMAIL). Kein hartcodierter Autor.
    env = {"PATH": f"/usr/bin:/bin:/usr/sbin:/sbin:{pathlib.Path.home()}/.local/bin",
           "HOME": str(pathlib.Path.home())}
    if os.environ.get("BRAIN_GIT_NAME"):
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = os.environ["BRAIN_GIT_NAME"]
    if os.environ.get("BRAIN_GIT_EMAIL"):
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = os.environ["BRAIN_GIT_EMAIL"]
    subprocess.run(["git", "add", str(path.relative_to(VAULT))], cwd=VAULT, check=True, env=env)
    r = subprocess.run(["git", "commit", "-m", message], cwd=VAULT, capture_output=True, text=True, env=env)
    out = (r.stdout + r.stderr).strip()
    if "nothing to commit" in out:
        return "keine Aenderung"
    return out.splitlines()[0] if out else "ok"


def qmd_query(q: str, n: int = 3):
    """qmd-Suche; liefert Liste von (pfad, score, titel)."""
    try:
        r = subprocess.run(
            ["qmd", "query", q, "-n", str(n)],
            cwd=VAULT, capture_output=True, text=True, timeout=45,
        )
    except Exception:
        return []
    hits = []
    cur = None
    for ln in r.stdout.splitlines():
        m = re.match(r"qmd://[^/]+/(.+?):\d+", ln)
        if m:
            cur = {"path": m.group(1)}
        m2 = re.search(r"Score:\s+(\d+)%", ln)
        if cur is not None and m2 and "score" not in cur:
            cur["score"] = m2.group(1) + "%"
        m3 = re.match(r"^Title:\s+(.*)$", ln)
        if cur is not None and m3 and "title" not in cur:
            cur["title"] = m3.group(1)
        if cur is not None and "score" in cur and "title" in cur:
            hits.append(cur); cur = None
        if len(hits) >= n:
            break
    return hits




def _load_graph():
    return json.loads(GRAPH.read_text())


def timeline_data():
    """first/last-seen je Notiz — mtime ist die einzige verfügbare Zeitquelle."""
    g = _load_graph()
    return {"items": [
        {"path": n["path"], "first": n.get("mtime"), "last": n.get("mtime")}
        for n in g.get("nodes", [])
    ]}


def similar_nodes(path: str, limit: int = 6):
    """Semantisch verwandte Notizen: qmd-Bedeutungssuche zum Titel;
    Fallback: gleiche Community (Label-Propagation über Wikilinks, v2-Index)."""
    g = _load_graph()
    by_path = {n["path"]: n for n in g.get("nodes", [])}
    me = by_path.get(path)
    if not me:
        return {"results": []}

    # 1) qmd — echte Bedeutungssuche (wie /agent)
    hits = []
    for h in qmd_query(me.get("title") or path, limit + 3):
        if h["path"] != path and h["path"] in by_path:
            hits.append(h)
        if len(hits) >= limit:
            break
    if hits:
        return {"results": [
            {"id": by_path[h["path"]]["id"],
             "title": h.get("title") or by_path[h["path"]]["title"],
             "path": h["path"], "score": h.get("score", "?")}
            for h in hits
        ]}

    # 2) Fallback: gleiche Community aus graph.json (schema v2)
    comm = me.get("community")
    if comm:
        peers = [n for n in g["nodes"]
                 if n.get("community") == comm and n["path"] != path]
        peers.sort(key=lambda n: (-n.get("degree", 0), n["id"]))
        return {"results": [
            {"id": n["id"], "title": n["title"], "path": n["path"],
             "score": "comm"} for n in peers[:limit]
        ]}
    return {"results": []}


def activity_data(limit: int = 12):
    """Letzte Vault-Änderungen aus dem Git-Log (Feed im Erweitert-Modus)."""
    r = subprocess.run(
        ["git", "log", "-n", str(limit * 2), "--name-only",
         "--pretty=format:%h\x1f%ci\x1f%s"],
        cwd=VAULT, capture_output=True, text=True,
    )
    items, cur = [], None
    for ln in r.stdout.splitlines():
        if "\x1f" in ln:
            h, ci, subj = ln.split("\x1f")
            cur = {"when": ci[:10], "detail": subj[:80], "kind": "commit"}
        elif ln.strip().endswith(".md") and cur:
            items.append({**cur, "title": pathlib.Path(ln.strip()).stem,
                          "path": ln.strip()})
            cur = None
        if len(items) >= limit:
            break
    return {"items": items}


def _db():
    """Read-only-Zugriff auf den SQLite-Index (WAL → nebenläufig zum Indexer ok)."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ---- Watcher + Push (S8, ADR-005) --------------------------------------------
# SSE statt WebSocket: stdlib-only, EventSource im Browser reconnectet selbst.
# Poll-Scan (10 s) + Debounce (2 ruhige Scans) statt inotify — keine neue Dep;
# Nightly-Timer bleibt Sicherheitsnetz.
SUBSCRIBERS = set()
SUB_LOCK = threading.Lock()


def sse_send(event: str, data: dict):
    payload = json.dumps(data, ensure_ascii=False)
    with SUB_LOCK:
        dead = []
        for h in SUBSCRIBERS:
            try:
                h.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode())
                h.wfile.flush()
            except Exception:
                dead.append(h)
        for h in dead:
            SUBSCRIBERS.discard(h)


def scan_mtimes(vault: pathlib.Path) -> dict:
    """Pfad → mtime aller md-Dateien (Änderungserkennung, grob aber billig)."""
    out = {}
    for f in vault.rglob("*.md"):
        try:
            out[str(f)] = f.stat().st_mtime
        except OSError:
            pass
    return out


def watcher_loop(interval=10.0):
    """Debounce: Änderungs-Burst abwarten, EINMAL reindizieren wenn ruhig."""
    prev = scan_mtimes(VAULT)
    dirty = False
    while True:
        time.sleep(interval)
        try:
            cur = scan_mtimes(VAULT)
            if cur != prev:
                prev = cur
                dirty = True
                continue  # noch in Bewegung (Syncthing-Schub, Editor, …)
            if not dirty:
                continue
            dirty = False
            # /note-POST hat evtl. frisch reindiziert — dann nicht doppelt
            try:
                if DB.exists() and prev and DB.stat().st_mtime >= max(prev.values()):
                    continue
            except ValueError:
                pass
            r = subprocess.run([sys.executable, str(HERE / "indexer.py"), str(VAULT)],
                               capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                print(f"[watcher] reindex fehlgeschlagen: {r.stderr[:200]}", flush=True)
                continue
            try:
                con = _db()
                m = {x["key"]: x["value"] for x in con.execute("SELECT key, value FROM meta")}
                con.close()
                sse_send("graph-updated", {
                    "generated_at": m.get("generated_at"),
                    "node_count": int(m.get("node_count", 0)),
                    "edge_count": int(m.get("edge_count", 0)),
                })
            except Exception as e:
                print(f"[watcher] push fehlgeschlagen: {e}", flush=True)
        except Exception as e:
            print(f"[watcher] {type(e).__name__}: {e}", flush=True)


def api_graph_meta():
    """Übersicht ohne Inhalte: Zählungen, Cluster, Ordner-Ebenen, Communities."""
    con = _db()
    try:
        meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
        return {
            "schema_version": int(meta.get("schema_version", 0)),
            "generated_at": meta.get("generated_at"),
            "node_count": int(meta.get("node_count", 0)),
            "edge_count": int(meta.get("edge_count", 0)),
            "unresolved": con.execute(
                "SELECT COUNT(*) FROM links_raw WHERE resolution IN ('ambiguous','missing')"
            ).fetchone()[0],
            "clusters": [dict(r) for r in con.execute(
                "SELECT cluster, color, COUNT(*) AS nodes, SUM(words) AS words "
                "FROM nodes GROUP BY cluster ORDER BY nodes DESC")],
            "folders": [dict(r) for r in con.execute(
                "SELECT path, parent, depth, nodes, words FROM folders "
                "ORDER BY depth, path")],
            "communities": [dict(r) for r in con.execute(
                "SELECT id, label, anchor, nodes, words, wikilinks FROM communities "
                "ORDER BY nodes DESC")],
        }
    finally:
        con.close()


def _node_row(r):
    return {"id": r["id"], "title": r["title"], "path": r["path"],
            "cluster": r["cluster"], "color": r["color"], "tags": (r["tags"] or "").split(","),
            "words": r["words"], "size": r["size"], "mtime": r["mtime"],
            "community": r["community"], "degree": r["degree"]}


def api_graph(folder=None, community=None, limit=1500):
    """LOD-Sicht (ADR-005): Ebene 0 = Top-Level-Ordner als Supernodes;
    folder=<pfad> = Unterordner + Notizen darunter (Nachfahren, nach Grad
    sortiert, gedeckelt); community=<id> = Mitglieder. Kanten nur innerhalb
    der jeweiligen Antwort (Budget B4)."""
    limit = max(1, min(int(limit), 1500))
    con = _db()
    try:
        nodes = []
        if folder:
            like = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
            rows = con.execute(
                "SELECT * FROM nodes WHERE id LIKE ? ESCAPE '\\' "
                "ORDER BY degree DESC, words DESC LIMIT ?", (like, limit)).fetchall()
            nodes = [_node_row(r) for r in rows]
            for r in con.execute("SELECT * FROM folders WHERE parent=? ORDER BY nodes DESC",
                                 (folder,)):
                nodes.append({"id": f"folder:{r['path']}", "title": r["path"], "path": r["path"],
                              "cluster": "Ordner", "color": "#4f8cff", "tags": [],
                              "words": r["words"], "size": 0, "mtime": None,
                              "community": None, "degree": r["nodes"]})
        elif community:
            rows = con.execute(
                "SELECT * FROM nodes WHERE community=? ORDER BY degree DESC, words DESC LIMIT ?",
                (community, limit)).fetchall()
            nodes = [_node_row(r) for r in rows]
        else:
            for r in con.execute("SELECT * FROM folders WHERE depth=1 ORDER BY nodes DESC"):
                nodes.append({"id": f"folder:{r['path']}", "title": r["path"], "path": r["path"],
                              "cluster": "Ordner", "color": "#4f8cff", "tags": [],
                              "words": r["words"], "size": 0, "mtime": None,
                              "community": None, "degree": r["nodes"]})

        edges = []
        if nodes and (folder or community):
            # Kanten innerhalb der Auswahl (Temp-Tabelle vermeidet Riesen-IN-Listen)
            con.execute("CREATE TEMP TABLE IF NOT EXISTS sel (id TEXT PRIMARY KEY)")
            con.execute("DELETE FROM sel")
            con.executemany("INSERT OR IGNORE INTO sel VALUES (?)",
                            [(n["id"],) for n in nodes])
            edges = [{"source": e[0], "target": e[1], "type": e[2]} for e in con.execute(
                "SELECT e.source, e.target, e.type FROM edges e "
                "JOIN sel s1 ON e.source=s1.id JOIN sel s2 ON e.target=s2.id")]
        if not folder and not community:
            # Ebene 0: Wikilink-Flüsse zwischen Top-Level-Ordnern
            flows = {}
            for a, b, w in con.execute(
                    "SELECT SUBSTR(n1.path,1,INSTR(n1.path||'/','/')-1), "
                    "SUBSTR(n2.path,1,INSTR(n2.path||'/','/')-1), COUNT(*) "
                    "FROM edges e JOIN nodes n1 ON e.source=n1.id JOIN nodes n2 ON e.target=n2.id "
                    "WHERE e.type='wikilink' AND n1.path<>n2.path GROUP BY 1,2"):
                if a != b:
                    flows[(a, b)] = flows.get((a, b), 0) + w
            edges = [{"source": f"folder:{a}", "target": f"folder:{b}",
                      "type": "wikilink", "weight": w} for (a, b), w in flows.items()]
        return {"schema_version": 2, "view": {"folder": folder, "community": community},
                "node_count": len(nodes), "nodes": nodes, "edges": edges}
    finally:
        con.close()


def remote_search(q: str, limit: int = 5):
    """Remote-Brain via Librarian (optional).
    Read-only (/query). Alle DB-Zugriffe nur über diesen Weg."""
    import urllib.request
    if not LIBRARIAN_URL or not LIBRARIAN_TOKEN:
        return {"error": "Librarian nicht konfiguriert (LIBRARIAN_URL/LIBRARIAN_TOKEN "
                         "fehlt — brain.env anlegen, siehe .env.example)"}
    token = LIBRARIAN_TOKEN
    base = LIBRARIAN_URL
    try:
        payload = json.dumps({"query": q, "agent": "zeratul"}).encode()
        req = urllib.request.Request(
            f"{base}/query",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        results = []
        # Antwort ist doppelt genestet: {ok, data:{ok, data:{results:[...]}}}
        node = data
        while isinstance(node, dict) and "results" not in node:
            node = node.get("data") or {}
        
        for hit in (node.get("results") or [])[:limit]:
            results.append({
                "content": (hit.get("content") or "")[:400],
                "agentId": hit.get("agentId"),
                "score": hit.get("score", 0),
                "id": hit.get("id"),
                "source": "librarian",
            })
        return {"results": results}
    except Exception as e:
        return {"error": f"Librarian nicht erreichbar ({type(e).__name__}: {e}) — Remote-Server pruefen (Logs/Verbindung)"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        # Bench-Zweitinstanzen (BRAIN_CORS=1): Browser darf graph von :8790 holen
        if os.environ.get("BRAIN_CORS") == "1":
            self.send_header("Access-Control-Allow-Origin", "*")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            # Info statt Verzeichnis-Listing (legacy/ ist nur Archiv)
            return self._json(200, {
                "service": "brain-indexer", "schema_version": 2,
                "endpoints": ["/api/v1/graph?folder=|community=", "/api/v1/graph/meta",
                              "/graph.json", "/note GET+POST", "/agent",
                              "/similar", "/timeline", "/hive"],
                "artefakte": {".brain/index.db": "SQLite-Index",
                              ".brain/graph.json": "kompatibler Export"},
            })
        if parsed.path == "/api/v1/events":
            # SSE-Stream: 'hello' sofort, 'graph-updated' nach Reindex,
            # Kommentar-Ping alle 15 s hält Proxys/Verbindungen offen.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with SUB_LOCK:
                SUBSCRIBERS.add(self)
            try:
                self.wfile.write(b"event: hello\ndata: {\"ok\":true}\n\n")
                self.wfile.flush()
                while True:
                    time.sleep(15)
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with SUB_LOCK:
                    SUBSCRIBERS.discard(self)
            return
        if parsed.path == "/api/v1/graph/meta":
            try:
                return self._json(200, api_graph_meta())
            except Exception as e:
                return self._json(500, {"error": f"api: {type(e).__name__}: {e} — indexer.py laufen lassen"})
        if parsed.path == "/api/v1/graph":
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                return self._json(200, api_graph(
                    folder=(qs.get("folder") or [None])[0],
                    community=(qs.get("community") or [None])[0],
                    limit=(qs.get("limit") or [1500])[0]))
            except Exception as e:
                return self._json(500, {"error": f"api: {type(e).__name__}: {e}"})
        if parsed.path == "/graph.json":
            try:
                body = GRAPH.read_bytes()
            except OSError:
                return self._json(500, {"error": "graph.json fehlt — indexer.py laufen lassen"})
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/note":
            qs = urllib.parse.parse_qs(parsed.query)
            rel = (qs.get("path") or [""])[0]
            try:
                p = vault_rel(rel)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            try:
                text = p.read_text()
            except OSError:
                return self._json(404, {"error": f"nicht gefunden: {rel}"})
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/timeline":
            return self._json(200, timeline_data())
        if parsed.path == "/activity":
            return self._json(200, activity_data())
        if parsed.path == "/similar":
            qs = urllib.parse.parse_qs(parsed.query)
            path = (qs.get("path") or [""])[0]
            if not path:
                return self._json(400, {"error": "path fehlt"})
            return self._json(200, similar_nodes(path))
        if parsed.path == "/hive":
            # /hive ist der stabile öffentliche API-Pfad (brain-galaxy Proxy);
            # Implementierung: remote_search() via Librarian.
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                return self._json(400, {"error": "leere Frage"})
            return self._json(200, remote_search(q))
        if parsed.path == "/agent":
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                return self._json(400, {"error": "leere Frage"})
            hits = qmd_query(q, 3)
            g = json.loads(GRAPH.read_text())
            by_path = {n["path"]: n for n in g["nodes"]}
            out = {"answer": None, "hit": None, "hits": []}
            for h in hits:
                n = by_path.get(h["path"])
                entry = {
                    "id": n["id"] if n else h["path"],
                    "title": h.get("title") or (n["title"] if n else h["path"]),
                    "path": h["path"], "score": h.get("score", "?"),
                }
                out["hits"].append(entry)
            if out["hits"]:
                best = out["hits"][0]
                out["hit"] = best
                out["answer"] = (
                    f"Beste Bedeutungs-Treffer fuer \"{html_mod.escape(q)}\": "
                    f"\"{best['title']}\" ({best['score']} Vertrauen). "
                    f"Weitere: " + ", ".join(f"{h['title']} ({h['score']})" for h in out["hits"][1:]) + "."
                )
            else:
                out["answer"] = "Die Bedeutungssuche hat keinen Treffer — versuch es konkreter."
            return self._json(200, out)
        super().do_GET()

    def do_POST(self):
        if self.path != "/note":
            return self._json(404, {"error": "unbekannter Endpunkt"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            rel = payload["path"]
            content = payload["content"]
        except Exception as e:
            return self._json(400, {"error": f"bad payload: {e}"})
        try:
            p = vault_rel(rel)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        if not isinstance(content, str):
            return self._json(400, {"error": "content muss String sein"})
        try:
            p.write_text(content)
            commit = git_commit(p, f"edit(ui): {rel} via Galaxy-Modal")
            subprocess.Popen(["python3", str(HERE / "indexer.py")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._json(200, {"ok": True, "commit": commit})
        except OSError as e:
            return self._json(500, {"error": str(e)})


class ThreadingServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    threading.Thread(target=watcher_loop, daemon=True, name="vault-watcher").start()
    with ThreadingServer(("127.0.0.1", PORT), functools.partial(Handler)) as httpd:
        print(f"Brain-Galaxy UI v4: http://localhost:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
