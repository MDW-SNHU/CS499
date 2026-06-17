import argparse
from pymongo import MongoClient
from datetime import datetime
import hashlib
import requests
import re
import time
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------
# Command-line argument parsing
# ---------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Build artists and songs collections with AudioDB enrichment.")
    parser.add_argument("--log", choices=["minimal", "normal", "verbose"], default="minimal",
                        help="Logging level (default: minimal)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of parallel workers for enrichment (default: 4)")
    parser.add_argument(
        "--rate",
        choices=["slow", "medium", "fast", "custom"],
        default="medium",
        help="Rate limit for AudioDB requests (default: medium)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Custom delay between AudioDB requests (seconds). Only used with --rate custom."
    )
    return parser.parse_args()


def resolve_delay(rate, custom_delay):
    if rate == "slow":
        return 1.0
    if rate == "medium":
        return 0.5
    if rate == "fast":
        return 0.25
    if rate == "custom":
        return custom_delay if custom_delay is not None else 0.5
    return 0.5


# ---------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------

def log_minimal(msg):
    print(msg)

def log_normal(msg, level):
    if level in ("normal", "verbose"):
        print(msg)

def log_verbose(msg, level):
    if level == "verbose":
        print(msg)


# ---------------------------------------------------------
# MongoDB configuration
# ---------------------------------------------------------

MONGO_URI = "mongodb://localhost:27017"

DB_NAME = "MongoTest"
SOURCE_COLLECTION = "billboard"

ARTISTS_COLLECTION = "artists"
SONGS_COLLECTION = "songs"

client = MongoClient(MONGO_URI)

source = client[DB_NAME][SOURCE_COLLECTION]
target_artists = client[DB_NAME][ARTISTS_COLLECTION]
target_songs = client[DB_NAME][SONGS_COLLECTION]


# ---------------------------------------------------------
# Disk-based caching
# ---------------------------------------------------------

CACHE_FILE = "audiodb_cache.json"
CACHE = {
    "artist": {},
    "track": {}
}

def load_cache(log_level):
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                CACHE["artist"] = data.get("artist", {})
                CACHE["track"] = data.get("track", {})
            log_normal(f"Loaded cache from {CACHE_FILE}", log_level)
        except Exception as e:
            log_normal(f"Failed to load cache: {e}", log_level)

def save_cache(log_level):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(CACHE, f)
        log_normal(f"Saved cache to {CACHE_FILE}", log_level)
    except Exception as e:
        log_normal(f"Failed to save cache: {e}", log_level)


# ---------------------------------------------------------
# Rate limiting for AudioDB
# ---------------------------------------------------------

AUDIO_DB_LOCK = threading.Lock()
LAST_REQUEST_TIME = 0.0
REQUEST_DELAY = 0.5  # will be set from CLI


def safe_request(url, log_level):
    global LAST_REQUEST_TIME

    with AUDIO_DB_LOCK:
        now = time.time()
        elapsed = now - LAST_REQUEST_TIME
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        LAST_REQUEST_TIME = time.time()

    for attempt in range(5):
        try:
            resp = requests.get(url, timeout=5)
            return resp
        except Exception as e:
            log_verbose(f"[WARN] Request failed (attempt {attempt+1}): {e}", log_level)
            time.sleep(2 ** attempt)

    return None


# ---------------------------------------------------------
# Artist name normalization
# ---------------------------------------------------------

def normalize_artist_name(name):
    n = name.lower()

    separators = [
        " with ", " & ", " and ", " featuring ", " feat. ", " feat ", " ft. ", " ft "
    ]

    for sep in separators:
        if sep in n:
            idx = n.index(sep)
            return name[:idx].strip()

    n = re.sub(r"^the\s+", "", n).strip()

    return n.title()


# ---------------------------------------------------------
# TheAudioDB API interface with caching
# ---------------------------------------------------------

AUDIO_DB_BASE = "https://theaudiodb.com/api/v1/json/2"

def fetch_artist_info(artist_name, log_level):
    key = artist_name.lower()

    if key in CACHE["artist"]:
        log_verbose(f"[CACHE] Artist: {artist_name}", log_level)
        return CACHE["artist"][key]

    url = f"{AUDIO_DB_BASE}/search.php?s={artist_name}"
    log_verbose(f"[API] Artist lookup: {url}", log_level)

    resp = safe_request(url, log_level)
    if resp is None:
        CACHE["artist"][key] = None
        return None

    try:
        data = resp.json()
    except Exception as e:
        log_verbose(f"[ERROR] JSON decode failed for artist {artist_name}: {e}", log_level)
        CACHE["artist"][key] = None
        return None

    if data and data.get("artists"):
        artist = data["artists"][0]
        CACHE["artist"][key] = artist
        return artist

    CACHE["artist"][key] = None
    return None


def fetch_song_info(artist_name, track_title, log_level):
    key = (artist_name.lower(), track_title.lower())

    if key in CACHE["track"]:
        log_verbose(f"[CACHE] Track: {artist_name} - {track_title}", log_level)
        return CACHE["track"][key]

    url = f"{AUDIO_DB_BASE}/searchtrack.php?s={artist_name}&t={track_title}"
    log_verbose(f"[API] Track lookup: {url}", log_level)

    resp = safe_request(url, log_level)
    if resp is None:
        CACHE["track"][key] = None
        return None

    try:
        data = resp.json()
    except Exception as e:
        log_verbose(f"[ERROR] JSON decode failed for track {artist_name} - {track_title}: {e}", log_level)
        CACHE["track"][key] = None
        return None

    if data and data.get("track"):
        track = data["track"][0]
        CACHE["track"][key] = track
        return track

    CACHE["track"][key] = None
    return None


# ---------------------------------------------------------
# Deterministic synthetic metadata for fallback
# ---------------------------------------------------------

GENRES = [
    "Pop", "Rock", "Hip-Hop", "R&B", "Country",
    "Jazz", "Electronic", "Folk", "Soul", "Metal"
]

COUNTRIES = [
    "US", "UK", "Canada", "Australia", "Germany",
    "France", "Sweden", "Brazil", "Japan", "South Korea"
]

def stable_hash(value: str) -> int:
    return int(hashlib.md5(value.encode("utf-8")).hexdigest(), 16)

def synthetic_genre(artist_name: str) -> str:
    return GENRES[stable_hash(artist_name) % len(GENRES)]

def synthetic_country(artist_name: str) -> str:
    return COUNTRIES[stable_hash(artist_name[::-1]) % len(COUNTRIES)]


# ---------------------------------------------------------
# Extract year from chart date
# ---------------------------------------------------------

def extract_year(date_value):
    if isinstance(date_value, datetime):
        return date_value.year

    if isinstance(date_value, str):
        try:
            return datetime.fromisoformat(date_value).year
        except ValueError:
            pass

    return None


# ---------------------------------------------------------
# Progress bar helper
# ---------------------------------------------------------

def progress_bar(current, total, prefix=""):
    percent = (current / total) * 100 if total else 0
    print(f"\r{prefix}{current}/{total} ({percent:5.1f}%)", end="", flush=True)


# ---------------------------------------------------------
# Batch insert helper
# ---------------------------------------------------------

def batch_insert(collection, docs, batch_size=500):
    batch = []
    for doc in docs:
        batch.append(doc)
        if len(batch) >= batch_size:
            collection.insert_many(batch)
            batch = []
    if batch:
        collection.insert_many(batch)


# ---------------------------------------------------------
# Build distinct artists with parallel enrichment
# ---------------------------------------------------------

def enrich_single_artist(artist_name, source_ids, log_level):
    info = fetch_artist_info(artist_name, log_level)

    if not info:
        normalized = normalize_artist_name(artist_name)
        if normalized != artist_name:
            log_verbose(f"Normalized artist: {artist_name} -> {normalized}", log_level)
            info = fetch_artist_info(normalized, log_level)

    if info:
        genre = info.get("strGenre")
        country = info.get("strCountry")
        enriched = True
    else:
        genre = synthetic_genre(artist_name)
        country = synthetic_country(artist_name)
        enriched = False

    return {
        "name": artist_name,
        "genre": genre,
        "country": country,
        "source_ids": source_ids,
        "enriched": enriched
    }


def build_distinct_artists(log_level, workers):
    total_docs = source.count_documents({})
    log_minimal(f"Total billboard records: {total_docs}")

    if total_docs == 0:
        log_minimal("No documents found in MongoTest.billboard. Aborting.")
        return

    log_minimal("Collecting distinct artists...")

    artist_map = {}
    count = 0
    for doc in source.find():
        artist_name = doc.get("artist")
        if not artist_name:
            continue
        if artist_name not in artist_map:
            artist_map[artist_name] = []
        artist_map[artist_name].append(doc.get("_id"))
        count += 1
        if count % 1000 == 0:
            progress_bar(count, total_docs, prefix="Scanning artists: ")

    print()

    artists = list(artist_map.items())
    total_artists = len(artists)
    log_minimal(f"Distinct artists: {total_artists}")

    log_minimal("Enriching artists in parallel...")
    enriched_docs = []
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(enrich_single_artist, name, ids, log_level): name
            for name, ids in artists
        }

        for future in as_completed(futures):
            doc = future.result()
            enriched_docs.append(doc)
            completed += 1
            if completed % 50 == 0 or completed == total_artists:
                progress_bar(completed, total_artists, prefix="Artists enriched: ")

    print()

    log_minimal(f"Inserting {len(enriched_docs)} artists in batches...")
    target_artists.delete_many({})
    batch_insert(target_artists, enriched_docs, batch_size=500)
    log_minimal("Artists insertion complete.")


# ---------------------------------------------------------
# Build distinct songs with parallel enrichment
# ---------------------------------------------------------

def enrich_single_song(title, artist_name, sample_doc, source_ids, log_level):
    chart_date = sample_doc.get("date")
    year = extract_year(chart_date)

    info = fetch_song_info(artist_name, title, log_level)

    if info:
        duration = info.get("intDuration")
        album = info.get("strAlbum")
        release_year = info.get("intYearReleased")
        enriched = True
    else:
        duration = sample_doc.get("duration")
        album = sample_doc.get("album")
        release_year = year
        enriched = False

    return {
        "title": title,
        "artist_name": artist_name,
        "album": album,
        "chart_year": year,
        "release_year": release_year,
        "duration": duration,
        "chart_metadata": {
            "this_week": sample_doc.get("this_week"),
            "last_week": sample_doc.get("last_week"),
            "peak_position": sample_doc.get("peak_position"),
            "weeks_on_chart": sample_doc.get("weeks_on_chart"),
        },
        "primary_source_ids": source_ids,
        "enriched": enriched
    }


def build_distinct_songs(log_level, workers):
    log_minimal("Collecting distinct songs...")

    song_map = {}
    total_docs = source.count_documents({})
    count = 0

    for doc in source.find():
        title = doc.get("song")
        artist_name = doc.get("artist")
        if not title or not artist_name:
            continue

        key = (title, artist_name)
        if key not in song_map:
            song_map[key] = {
                "sample_doc": doc,
                "source_ids": [doc.get("_id")]
            }
        else:
            song_map[key]["source_ids"].append(doc.get("_id"))

        count += 1
        if count % 1000 == 0:
            progress_bar(count, total_docs, prefix="Scanning songs: ")

    print()

    items = list(song_map.items())
    total_songs = len(items)
    log_minimal(f"Distinct songs: {total_songs}")

    log_minimal("Enriching songs in parallel...")
    enriched_docs = []
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                enrich_single_song,
                title,
                artist_name,
                data["sample_doc"],
                data["source_ids"],
                log_level
            ): (title, artist_name)
            for (title, artist_name), data in items
        }

        for future in as_completed(futures):
            doc = future.result()
            enriched_docs.append(doc)
            completed += 1
            if completed % 100 == 0 or completed == total_songs:
                progress_bar(completed, total_songs, prefix="Songs enriched: ")

    print()

    log_minimal(f"Inserting {len(enriched_docs)} songs in batches...")
    target_songs.delete_many({})
    batch_insert(target_songs, enriched_docs, batch_size=500)
    log_minimal("Songs insertion complete.")


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    log_level = args.log
    workers = args.workers
    delay = resolve_delay(args.rate, args.delay)

    REQUEST_DELAY = delay

    log_minimal(f"Starting pipeline (log level: {log_level}, workers: {workers}, delay: {REQUEST_DELAY}s)")

    load_cache(log_level)

    try:
        build_distinct_artists(log_level, workers)
        save_cache(log_level)

        build_distinct_songs(log_level, workers)
        save_cache(log_level)

        log_minimal("Pipeline complete.")
    except KeyboardInterrupt:
        log_minimal("\nInterrupted by user. Saving cache and exiting.")
        save_cache(log_level)
    except Exception as e:
        log_minimal(f"\nUnexpected error: {e}. Saving cache and exiting.")
        save_cache(log_level)
