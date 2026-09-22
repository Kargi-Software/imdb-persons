#!/usr/bin/env python3
"""
Build ultra-compact IMDb person filmography shards for Cloudflare Worker.

Output structure:
  persons/
    00/
      00000.json
      00001.json
      ...
  version.json

Format of each credit:
  [tconst, category_short, character|null, primaryTitle, year]
"""

from __future__ import annotations

import gzip
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.request import urlretrieve

# ─────────────────────────── Config ───────────────────────────

DATASETS = {
    "basics": "https://datasets.imdbws.com/title.basics.tsv.gz",
    "principals": "https://datasets.imdbws.com/title.principals.tsv.gz",
}

EXCLUDE_TITLE_TYPES: Set[str] = {
    "tvEpisode",
}

# فقط این categoryها نگه داشته می‌شن
ALLOWED_CATEGORIES: Set[str] = {
    "actor",
    "actress",
    "director",
    "writer",
    "producer",
    "cinematographer",
    "editor",
    "composer",
    "production_designer",
    "costume_designer",
    "make_up",
}

CATEGORY_SHORT: Dict[str, str] = {
    "actor": "a",
    "actress": "a",
    "director": "d",
    "writer": "w",
    "producer": "p",
    "cinematographer": "c",
    "editor": "e",
    "composer": "m",
    "production_designer": "pd",
    "costume_designer": "cs",
    "make_up": "mu",
}

OUTPUT_DIR = Path("persons")
VERSION_FILE = Path("version.json")
CACHE_DIR = Path("cache")
MAX_CHARS_LEN = 80


# ─────────────────────────── Helpers ───────────────────────────

def download_if_needed(name: str, url: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    dest = CACHE_DIR / f"{name}.tsv.gz"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"✓ {name} already cached ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest
    print(f"↓ Downloading {name} ...")
    urlretrieve(url, dest)
    print(f"  → {dest.stat().st_size / 1e6:.1f} MB")
    return dest


def parse_year(val: str) -> Optional[int]:
    if not val or val == "\\N":
        return None
    try:
        y = int(val)
        return y if 1870 <= y <= 2035 else None
    except ValueError:
        return None


def parse_characters(raw: str) -> Optional[str]:
    if not raw or raw == "\\N":
        return None
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]

    parts = []
    current = ""
    in_quote = False
    for ch in raw:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch == "," and not in_quote:
            if current.strip():
                parts.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        parts.append(current.strip())

    if not parts:
        return None

    name = parts[0].strip().strip('"').strip("'")
    if not name or name.lower() in {"self", "himself", "herself"}:
        return None
    if len(name) > MAX_CHARS_LEN:
        name = name[: MAX_CHARS_LEN - 1] + "…"
    return name or None


def get_shard_paths(nconst: str) -> Tuple[str, str]:
    """
    nm0000138 → numeric = 0000138
    folder  = 00
    file    = 00001.json
    """
    numeric = nconst[2:]
    if len(numeric) < 5:
        numeric = numeric.zfill(5)
    shard5 = numeric[:5].ljust(5, "0")
    prefix2 = shard5[:2]
    return prefix2, shard5


# ─────────────────────────── Main build ───────────────────────────

def load_title_basics(path: Path) -> Dict[str, Tuple[str, Optional[int]]]:
    print("Loading title.basics ...")
    titles: Dict[str, Tuple[str, Optional[int]]] = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline()
        for i, line in enumerate(f, 1):
            if i % 2_000_000 == 0:
                print(f"  basics processed {i:,} rows ...")
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            tconst, title_type, primary_title, _, _, start_year = parts[:6]
            if title_type in EXCLUDE_TITLE_TYPES:
                continue
            year = parse_year(start_year)
            if primary_title and primary_title != "\\N":
                titles[tconst] = (primary_title, year)
    print(f"  → kept {len(titles):,} titles")
    return titles


def build_filmography(
    principals_path: Path,
    titles: Dict[str, Tuple[str, Optional[int]]],
) -> Dict[str, List[list]]:
    print("Building filmography from title.principals ...")
    filmography: Dict[str, List[list]] = defaultdict(list)

    with gzip.open(principals_path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline()
        for i, line in enumerate(f, 1):
            if i % 5_000_000 == 0:
                print(f"  principals processed {i:,} rows | persons so far: {len(filmography):,}")

            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue

            tconst, ordering, nconst, category, job, characters = parts[:6]

            if category not in ALLOWED_CATEGORIES:
                continue
            if tconst not in titles:
                continue

            title, year = titles[tconst]
            cat_short = CATEGORY_SHORT.get(category, category[:3])

            char = None
            if category in {"actor", "actress"}:
                char = parse_characters(characters)

            entry = [tconst, cat_short, char, title, year]
            filmography[nconst].append(entry)

    print(f"  → {len(filmography):,} persons with at least one credit")
    return filmography


def write_shards(filmography: Dict[str, List[list]]) -> None:
    print("Writing shards ...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    shards: Dict[Tuple[str, str], Dict[str, List[list]]] = defaultdict(dict)

    for nconst, credits in filmography.items():
        # سورت بر اساس سال (جدید → قدیم)
        credits.sort(key=lambda x: (x[4] is None, -(x[4] or 0), x[0]))
        prefix, shard = get_shard_paths(nconst)
        shards[(prefix, shard)][nconst] = credits

    total_files = 0
    for (prefix, shard), data in sorted(shards.items()):
        folder = OUTPUT_DIR / prefix
        folder.mkdir(parents=True, exist_ok=True)
        out_path = folder / f"{shard}.json"

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

        total_files += 1
        if total_files % 500 == 0:
            print(f"  written {total_files} shard files ...")

    print(f"  → {total_files} shard files written under {OUTPUT_DIR}/")


def write_version() -> None:
    version = {
        "updated": time.strftime("%Y-%m-%d"),
        "version": time.strftime("%Y%m%d"),
        "source": "imdbws title.principals + title.basics",
        "note": "ultra-compact filmography (actor/director/writer/producer/... only, no tvEpisode)",
    }
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(version, f, indent=2)
    print(f"✓ {VERSION_FILE} written")


def main() -> None:
    t0 = time.time()
    print("=" * 60)
    print("IMDb Filmography Builder (ultra-compact + heavy sharding)")
    print("=" * 60)

    basics_gz = download_if_needed("basics", DATASETS["basics"])
    principals_gz = download_if_needed("principals", DATASETS["principals"])

    titles = load_title_basics(basics_gz)
    filmography = build_filmography(principals_gz, titles)

    del titles  # آزاد کردن حافظه

    write_shards(filmography)
    write_version()

    elapsed = time.time() - t0
    print("=" * 60)
    print(f"Done in {elapsed / 60:.1f} minutes")
    print(f"Output: {OUTPUT_DIR.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
