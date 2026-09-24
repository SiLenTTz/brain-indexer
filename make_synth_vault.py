#!/usr/bin/env python3
"""Deterministischer Synth-Vault für Skalierungs-Benches (S10, Budget B1/B3/B6).

Erzeugt N Notizen über reale Top-Level-Ordner mit realistischer Wikilink-
Verteilung (die meisten Notizen 0-2 Links, wenige Hubs 7-25) und eindeutigen
Stems — damit die Obsidian-Auflösung im Indexer sauber durchläuft.

Aufruf: python3 make_synth_vault.py <ziel-pfad> [N]   (Default N=10000)
"""
import pathlib
import random
import sys

FOLDERS = [
    "00_inbox", "10_archive", "20_systems", "30_topics", "40_projects",
    "50_jobs", "90_archive", "99_templates", "docs",
]

TOPICS = ["System", "Archive", "Topics", "Projects", "Work"]


def main():
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/synth-vault")
    n = int(sys.argv[2] if len(sys.argv) > 2 else 10000)
    rnd = random.Random(20260910)  # deterministisch

    target.mkdir(parents=True, exist_ok=True)
    notes = []  # (ordner, dateiname-ohne-ext)

    # Grad-Verteilung: 70% niedrig (0-2), 27% mittel (3-6), 3% Hubs (7-25)
    def target_degree(i):
        r = rnd.random()
        if r < 0.70:
            return rnd.randint(0, 2)
        if r < 0.97:
            return rnd.randint(3, 6)
        return rnd.randint(7, 25)

    degs = [target_degree(i) for i in range(n)]

    for i in range(n):
        folder = FOLDERS[i % len(FOLDERS)]
        name = f"note-{i:05d}"
        (target / folder).mkdir(exist_ok=True)
        notes.append((folder, name))

    # Links: Ziel-zufällig, gleiche-Ordner-Bias (wie echte Vaults)
    by_folder = {}
    for f, x in notes:
        by_folder.setdefault(f, []).append(x)

    for i, (folder, name) in enumerate(notes):
        links = []
        for _ in range(degs[i]):
            if rnd.random() < 0.55 and len(by_folder[folder]) > 1:
                cand = rnd.choice(by_folder[folder])
                if cand != name:
                    links.append(cand)
            else:
                j = rnd.randrange(n)
                if notes[j][1] != name:
                    links.append(notes[j][1])
        body = "\n".join(f"- siehe [[{l}]]" for l in sorted(set(links)))
        topic = TOPICS[i % len(TOPICS)]
        (target / folder / f"{name}.md").write_text(
            f"# {name}\n\ncluster: {topic}\n\n{body}\n", encoding="utf-8"
        )

    print(f"{n} Notizen in {target} (Grad-Summe {sum(degs)})")


if __name__ == "__main__":
    main()
