#!/usr/bin/env python3
"""
Build ultra-compact IMDb person filmography shards.

Output:
  persons/
    00/
      00000.json
      ...
  version.json

Format of each credit:
  [tconst, category_short, character|null, primaryTitle, startYear, endYear]
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
    "episode": "https://datasets.imdbws.com/title.episode.tsv.gz",
}

# این‌ها را کامل رد می‌کنیم (به جز writer/producer که جداگانه هندل می‌شوند)
EXCLUDE_TITLE_TYPES: Set[str] = {
    "tvEpisode",
}

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

# فقط این دو دسته را از اپیزودها هم می‌گیریم و به parent منتقل می‌کنیم
EPISODE_ALLOWED_CATEGORIES: Set[str] = {
    "writer",
    "producer",
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
    numeric = nconst[2:]
    if len(numeric) < 5:
        numeric = numeric.zfill(5)
    shard5 = numeric[:5].ljust(5, "0")
    prefix2 = shard5[:2]
    return prefix2, shard5


# ─────────────────────────── Loaders ───────────────────────────

def load_title_basics(path: Path) -> Dict[str, Tuple[str, str, Optional[int], Optional[int]]]:
    """
    Returns: tconst → (titleType, primaryTitle, startYear, endYear)
    """
    print("Loading title.basics ...")
    titles: Dict[str, Tuple[str, str, Optional[int], Optional[int]]] = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline()
        for i, line in enumerate(f, 1):
            if i % 2_000_000 == 0:
                print(f"  basics processed {i:,} rows ...")
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            tconst, title_type, primary_title, _, _, start_year, end_year = parts[:7]
            start = parse_year(start_year)
            end = parse_year(end_year)
            if primary_title and primary_title != "\\N":
                titles[tconst] = (title_type, primary_title, start, end)
    print(f"  → kept {len(titles):,} titles")
    return titles


def load_episode_parents(path: Path) -> Dict[str, str]:
    """
    Returns: episode_tconst → parent_tconst
    """
    print("Loading title.episode ...")
    parents: Dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline()
        for i, line in enumerate(f, 1):
            if i % 1_000_000 == 0:
                print(f"  episode processed {i:,} rows ...")
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            episode_id, parent_id = parts[0], parts[1]
            if parent_id and parent_id != "\\N":
                parents[episode_id] = parent_id
    print(f"  → {len(parents):,} episode → parent mappings")
    return parents


def build_filmography(
    principals_path: Path,
    titles: Dict[str, Tuple[str, str, Optional[int], Optional[int]]],
    episode_parents: Dict[str, str],
) -> Dict[str, List[list]]:
    print("Building filmography from title.principals ...")
    filmography: Dict[str, List[list]] = defaultdict(list)

    # برای جلوگیری از تکراری شدن اعتبار writer/producer روی یک سریال
    seen: Set[Tuple[str, str, str]] = set()  # (nconst, parent_tconst, category)

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

            # --- حالت اپیزود ---
            if tconst in episode_parents:
                # فقط writer و producer را از اپیزودها می‌گیریم
                if category not in EPISODE_ALLOWED_CATEGORIES:
                    continue

                parent_id = episode_parents[tconst]
                if parent_id not in titles:
                    continue

                # جلوگیری از تکرار روی یک سریال
                key = (nconst, parent_id, category)
                if key in seen:
                    continue
                seen.add(key)

                title_type, primary_title, start_year, end_year = titles[parent_id]
                target_tconst = parent_id
            else:
                # عنوان‌های عادی (فیلم، سریال، مینی‌سریال و ...)
                if tconst not in titles:
                    continue
                title_type, primary_title, start_year, end_year = titles[tconst]

                # اپیزودهایی که به هر دلیلی parent ندارند را رد کن
                if title_type in EXCLUDE_TITLE_TYPES:
                    continue

                target_tconst = tconst

            cat_short = CATEGORY_SHORT.get(category, category[:2])

            char = None
            if category in {"actor", "actress"}:
                char = parse_characters(characters)

            entry = [target_tconst, cat_short, char, primary_title, start_year, end_year]
            filmography[nconst].append(entry)

    print(f"  → {len(filmography):,} persons with at least one credit")
    return filmography


def write_shards(filmography: Dict[str, List[list]]) -> None:
    print("Writing shards ...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    shards: Dict[Tuple[str, str], Dict[str, List[list]]] = defaultdict(dict)

    for nconst, credits in filmography.items():
        # سورت بر اساس startYear (جدید → قدیم)
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
        "source": "imdbws title.principals + title.basics + title.episode",
        "note": "writers & producers from episodes are mapped to parent series",
    }
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(version, f, indent=2)
    print(f"✓ {VERSION_FILE} written")


def main() -> None:
    t0 = time.time()
    print("=" * 60)
    print("IMDb Filmography Builder (with episode writers → parent)")
    print("=" * 60)

    basics_gz = download_if_needed("basics", DATASETS["basics"])
    principals_gz = download_if_needed("principals", DATASETS["principals"])
    episode_gz = download_if_needed("episode", DATASETS["episode"])

    titles = load_title_basics(basics_gz)
    episode_parents = load_episode_parents(episode_gz)

    filmography = build_filmography(principals_gz, titles, episode_parents)

    del titles
    del episode_parents

    write_shards(filmography)
    write_version()

    elapsed = time.time() - t0
    print("=" * 60)
    print(f"Done in {elapsed / 60:.1f} minutes")
    print(f"Output: {OUTPUT_DIR.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
