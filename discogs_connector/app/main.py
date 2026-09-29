#!/usr/bin/env python3
"""Home Assistant ingress app for a persistent Discogs collection browser."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests
from PIL import Image
from io import BytesIO

OPTIONS_PATH = Path("/data/options.json")
DATABASE_PATH = Path("/share/home_apps.sqlite3")
LEGACY_DATABASE_PATHS = (
    Path("/share/discogs_connector/discogs.sqlite3"),
    Path("/data/discogs.sqlite3"),
)
API_ROOT = "https://api.discogs.com"
PORT = 8099
MAX_PAGES = 100
RANDOM_PICK_EVENT = "discogs_random_pick_requested"
RANDOM_PICK_ENTITY = "sensor.discogs_random_pick"


def log(message: str, *, error: BaseException | None = None) -> None:
    """Write timestamped, actionable app logs without exposing request headers."""
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if error is None:
        print(f"[{timestamp}] {message}", flush=True)
        return
    response = getattr(error, "response", None)
    request = getattr(error, "request", None)
    details = [f"{type(error).__name__}: {error}"]
    if response is not None:
        details.append(f"HTTP {getattr(response, 'status_code', '?')}")
        url = getattr(response, "url", None) or getattr(request, "url", None)
        if url:
            details.append(f"url={url}")
        body = str(getattr(response, "text", "") or "").strip()
        if body:
            details.append(f"response={body[:500]}")
    print(f"[{timestamp}] ERROR {message}: {'; '.join(details)}", flush=True)
    if not isinstance(error, requests.HTTPError):
        traceback.print_exception(type(error), error, error.__traceback__)


def read_options() -> dict[str, Any]:
    try:
        return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class CollectionDatabase:
    """Shared SQLite file with namespaced Discogs tables and per-app migrations."""

    APP_ID = "discogs_connector"
    SCHEMA_VERSION = 4

    def __init__(self, path: Path, legacy_paths: tuple[Path, ...] = ()) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_paths = tuple(p for p in legacy_paths if p != path)
        setup = sqlite3.connect(self.path, timeout=20)
        try:
            setup.execute("PRAGMA busy_timeout = 20000")
            setup.execute("PRAGMA journal_mode = WAL")
        finally:
            setup.close()
        with self.connect() as connection:
            self._create_schema(connection)
            self._migrate(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 20000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_schema_versions (
                app_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discogs_collection_sync (
                username TEXT PRIMARY KEY,
                synced_at REAL NOT NULL,
                total INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discogs_releases (
                release_id INTEGER PRIMARY KEY,
                master_id INTEGER,
                title TEXT NOT NULL,
                year INTEGER,
                country TEXT,
                released TEXT,
                uri TEXT NOT NULL DEFAULT '',
                resource_url TEXT NOT NULL DEFAULT '',
                thumb TEXT NOT NULL DEFAULT '',
                cover_image TEXT NOT NULL DEFAULT '',
                notes TEXT,
                fetched_at REAL
            );
            CREATE TABLE IF NOT EXISTS discogs_masters (
                master_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                year INTEGER,
                main_release_id INTEGER,
                artwork_url TEXT NOT NULL DEFAULT '',
                thumb_url TEXT NOT NULL DEFAULT '',
                fetched_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discogs_metadata_failures (
                entity_type TEXT NOT NULL CHECK (entity_type IN ('release', 'master')),
                entity_id INTEGER NOT NULL,
                status_code INTEGER NOT NULL,
                attempted_at REAL NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (entity_type, entity_id)
            );
            CREATE TABLE IF NOT EXISTS discogs_collection_entries (
                instance_id INTEGER PRIMARY KEY,
                release_id INTEGER NOT NULL REFERENCES discogs_releases(release_id),
                date_added TEXT NOT NULL DEFAULT '',
                folder_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_discogs_collection_release
                ON discogs_collection_entries(release_id);
            CREATE TABLE IF NOT EXISTS discogs_artists (
                artist_key INTEGER PRIMARY KEY,
                discogs_artist_id INTEGER UNIQUE,
                name TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_discogs_artist_name ON discogs_artists(name COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS discogs_release_artists (
                release_id INTEGER NOT NULL REFERENCES discogs_releases(release_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                artist_key INTEGER NOT NULL REFERENCES discogs_artists(artist_key),
                anv TEXT NOT NULL DEFAULT '',
                join_text TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (release_id, position)
            );
            CREATE TABLE IF NOT EXISTS discogs_labels (
                label_key INTEGER PRIMARY KEY,
                discogs_label_id INTEGER UNIQUE,
                name TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_discogs_label_name ON discogs_labels(name COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS discogs_release_labels (
                release_id INTEGER NOT NULL REFERENCES discogs_releases(release_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                label_key INTEGER NOT NULL REFERENCES discogs_labels(label_key),
                catalog_number TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (release_id, position)
            );
            CREATE INDEX IF NOT EXISTS idx_discogs_catalog_number
                ON discogs_release_labels(catalog_number COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS discogs_release_formats (
                release_id INTEGER NOT NULL REFERENCES discogs_releases(release_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '',
                format_text TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (release_id, position)
            );
            CREATE TABLE IF NOT EXISTS discogs_format_descriptions (
                release_id INTEGER NOT NULL,
                format_position INTEGER NOT NULL,
                position INTEGER NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (release_id, format_position, position),
                FOREIGN KEY (release_id, format_position)
                    REFERENCES discogs_release_formats(release_id, position) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS discogs_release_classifications (
                release_id INTEGER NOT NULL REFERENCES discogs_releases(release_id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('genre', 'style')),
                value TEXT NOT NULL,
                PRIMARY KEY (release_id, kind, value)
            );
            CREATE INDEX IF NOT EXISTS idx_discogs_classification_value
                ON discogs_release_classifications(kind, value COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS discogs_tracks (
                track_key TEXT PRIMARY KEY,
                release_id INTEGER NOT NULL REFERENCES discogs_releases(release_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                discogs_track_id TEXT,
                position TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                duration TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER,
                track_type TEXT NOT NULL DEFAULT 'track',
                parent_sequence INTEGER,
                UNIQUE (release_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_discogs_track_title
                ON discogs_tracks(title COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_discogs_track_release_order
                ON discogs_tracks(release_id, sequence);
            CREATE TABLE IF NOT EXISTS discogs_track_credits (
                track_key TEXT NOT NULL REFERENCES discogs_tracks(track_key) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                artist_key INTEGER NOT NULL REFERENCES discogs_artists(artist_key),
                role TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (track_key, position)
            );
            CREATE TABLE IF NOT EXISTS discogs_release_credits (
                release_id INTEGER NOT NULL REFERENCES discogs_releases(release_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                artist_key INTEGER NOT NULL REFERENCES discogs_artists(artist_key),
                role TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (release_id, position)
            );
            CREATE TABLE IF NOT EXISTS discogs_release_payloads (
                release_id INTEGER PRIMARY KEY REFERENCES discogs_releases(release_id) ON DELETE CASCADE,
                fetched_at REAL NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _tables(connection: sqlite3.Connection, schema: str = "main") -> set[str]:
        return {row[0] for row in connection.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type='table'"
        )}

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

    def _migrate(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT version FROM app_schema_versions WHERE app_id = ?", (self.APP_ID,)
        ).fetchone()
        version = int(row["version"]) if row else 0
        if version > self.SCHEMA_VERSION:
            raise RuntimeError(f"Discogs schema version {version} is newer than this app supports.")
        if version == 0:
            if self._is_legacy_schema(connection):
                connection.execute("BEGIN")
                self._import_legacy_tables(connection, "main")
                self._archive_legacy_tables(connection)
            else:
                for legacy_path in self.legacy_paths:
                    if legacy_path.is_file():
                        connection.execute("BEGIN")
                        self._import_legacy_file(connection, legacy_path)
                        break
                self._archive_legacy_tables(connection)
            self._set_schema_version(connection, self.SCHEMA_VERSION)
        elif version < self.SCHEMA_VERSION:
            self._set_schema_version(connection, self.SCHEMA_VERSION)

    @staticmethod
    def _archive_legacy_tables(connection: sqlite3.Connection) -> None:
        """Keep a namespaced copy of legacy tables after normalized migration."""
        for table in ("collection_sync", "collection_entries", "release_details"):
            if table in CollectionDatabase._tables(connection):
                archived = f"discogs_legacy_v1_{table}"
                if archived not in CollectionDatabase._tables(connection):
                    connection.execute(f"ALTER TABLE {table} RENAME TO {archived}")

    @staticmethod
    def _is_legacy_schema(connection: sqlite3.Connection, schema: str = "main") -> bool:
        tables = CollectionDatabase._tables(connection, schema)
        return "collection_entries" in tables and {
            "instance_id", "release_id", "formats_json", "labels_json", "catalog_numbers_json"
        }.issubset(CollectionDatabase._columns(connection, "collection_entries"))

    def _set_schema_version(self, connection: sqlite3.Connection, version: int) -> None:
        connection.execute(
            """INSERT INTO app_schema_versions (app_id, version, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(app_id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at""",
            (self.APP_ID, version, time.time()),
        )

    def _import_legacy_file(self, connection: sqlite3.Connection, path: Path) -> None:
        source = sqlite3.connect(path, timeout=15)
        source.row_factory = sqlite3.Row
        try:
            source_tables = self._tables(source)
            if "collection_entries" not in source_tables:
                return
            sync_row = source.execute("SELECT * FROM collection_sync LIMIT 1").fetchone() if "collection_sync" in source_tables else None
            entry_rows = source.execute("SELECT * FROM collection_entries").fetchall()
            details = source.execute("SELECT * FROM release_details").fetchall() if "release_details" in source_tables else []
            self._import_legacy_rows(connection, entry_rows, sync_row, details)
        finally:
            source.close()

    def _import_legacy_tables(self, connection: sqlite3.Connection, schema: str) -> None:
        sync_row = None
        if "collection_sync" in self._tables(connection, schema):
            sync_row = connection.execute(f"SELECT * FROM {schema}.collection_sync LIMIT 1").fetchone()
        entry_rows = connection.execute(f"SELECT * FROM {schema}.collection_entries").fetchall()
        details = connection.execute(f"SELECT * FROM {schema}.release_details").fetchall() if "release_details" in self._tables(connection, schema) else []
        self._import_legacy_rows(connection, entry_rows, sync_row, details)

    def _import_legacy_rows(
        self,
        connection: sqlite3.Connection,
        entry_rows: list[sqlite3.Row],
        sync_row: sqlite3.Row | None,
        details: list[sqlite3.Row],
    ) -> None:
        import itertools
        for entry in entry_rows:
            row = dict(entry)
            release_id = int(row["release_id"])
            label_names = json.loads(row.get("labels_json") or "[]")
            catalog_numbers = json.loads(row.get("catalog_numbers_json") or "[]")
            labels = [
                {"name": name, "catno": catno}
                for name, catno in itertools.zip_longest(label_names, catalog_numbers, fillvalue="")
                if name or catno
            ]
            self._save_release_metadata(connection, {
                "id": release_id, "title": row.get("title", ""), "year": row.get("year"),
                "artists": ([{"name": row["artist"]}] if row.get("artist") else []),
                "labels": labels,
                "formats": [{"name": value} for value in json.loads(row.get("formats_json") or "[]")],
                "thumb": row.get("thumb", ""), "cover_image": row.get("cover_image", ""),
                "resource_url": row.get("resource_url", ""), "uri": row.get("uri", ""),
            })
            connection.execute(
                """INSERT OR REPLACE INTO discogs_collection_entries
                   (instance_id, release_id, date_added, folder_id) VALUES (?, ?, ?, ?)""",
                (row.get("instance_id") or release_id, release_id, row.get("date_added", ""), row.get("folder_id")),
            )
        if sync_row:
            sync = dict(sync_row)
            username = sync.get("username", "IPAIRIS")
            connection.execute(
                """INSERT OR REPLACE INTO discogs_collection_sync (username, synced_at, total) VALUES (?, ?, ?)""",
                (username, sync.get("synced_at", time.time()), sync.get("total", len(entry_rows))),
            )
        for detail in details:
            row = dict(detail)
            try:
                payload = json.loads(row["payload_json"])
            except (KeyError, json.JSONDecodeError, TypeError):
                continue
            self.save_release_details(int(row["release_id"]), payload, connection=connection,
                                      fetched_at=float(row.get("fetched_at", time.time())))

    @staticmethod
    def _duration_milliseconds(duration: str) -> int | None:
        if not duration:
            return None
        try:
            parts = [int(part) for part in duration.split(":")]
            total = 0
            for part in parts:
                total = total * 60 + part
            return total * 1000
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _artist_key(connection: sqlite3.Connection, artist: dict[str, Any]) -> int:
        name = str(artist.get("name") or "Unknown artist")
        discogs_id = artist.get("id")
        if discogs_id not in (None, 0, "0", ""):
            connection.execute(
                """INSERT INTO discogs_artists (discogs_artist_id, name) VALUES (?, ?)
                   ON CONFLICT(discogs_artist_id) DO UPDATE SET name=excluded.name""",
                (int(discogs_id), name),
            )
            return int(connection.execute(
                "SELECT artist_key FROM discogs_artists WHERE discogs_artist_id = ?", (int(discogs_id),)
            ).fetchone()[0])
        existing = connection.execute(
            "SELECT artist_key FROM discogs_artists WHERE discogs_artist_id IS NULL AND name = ?", (name,)
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = connection.execute(
            "INSERT INTO discogs_artists (discogs_artist_id, name) VALUES (NULL, ?)", (name,)
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _label_key(connection: sqlite3.Connection, label: dict[str, Any]) -> int:
        name = str(label.get("name") or "Unknown label")
        discogs_id = label.get("id")
        if discogs_id not in (None, 0, "0", ""):
            connection.execute(
                """INSERT INTO discogs_labels (discogs_label_id, name) VALUES (?, ?)
                   ON CONFLICT(discogs_label_id) DO UPDATE SET name=excluded.name""",
                (int(discogs_id), name),
            )
            return int(connection.execute(
                "SELECT label_key FROM discogs_labels WHERE discogs_label_id = ?", (int(discogs_id),)
            ).fetchone()[0])
        existing = connection.execute(
            "SELECT label_key FROM discogs_labels WHERE discogs_label_id IS NULL AND name = ?", (name,)
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = connection.execute(
            "INSERT INTO discogs_labels (discogs_label_id, name) VALUES (NULL, ?)", (name,)
        )
        return int(cursor.lastrowid)

    def _save_release_metadata(
        self, connection: sqlite3.Connection, release: dict[str, Any], fetched_at: float | None = None
    ) -> None:
        release_id = int(release["id"])
        connection.execute(
            """INSERT INTO discogs_releases (
                 release_id, master_id, title, year, country, released, uri, resource_url, thumb, cover_image, notes, fetched_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(release_id) DO UPDATE SET
                 master_id=COALESCE(excluded.master_id, discogs_releases.master_id),
                 title=excluded.title, year=COALESCE(excluded.year, discogs_releases.year),
                 country=COALESCE(excluded.country, discogs_releases.country),
                 released=COALESCE(excluded.released, discogs_releases.released),
                 uri=CASE WHEN excluded.uri='' THEN discogs_releases.uri ELSE excluded.uri END,
                 resource_url=CASE WHEN excluded.resource_url='' THEN discogs_releases.resource_url ELSE excluded.resource_url END,
                 thumb=CASE WHEN excluded.thumb='' THEN discogs_releases.thumb ELSE excluded.thumb END,
                 cover_image=CASE WHEN excluded.cover_image='' THEN discogs_releases.cover_image ELSE excluded.cover_image END,
                 notes=COALESCE(excluded.notes, discogs_releases.notes),
                 fetched_at=COALESCE(excluded.fetched_at, discogs_releases.fetched_at)""",
            (release_id, release.get("master_id"), str(release.get("title") or ""), release.get("year"),
             release.get("country"), release.get("released"), release.get("uri") or "",
             release.get("resource_url") or "", release.get("thumb") or "", release.get("cover_image") or "",
             release.get("notes"), fetched_at),
        )
        for table in ("discogs_release_artists", "discogs_release_labels", "discogs_release_formats",
                      "discogs_release_classifications"):
            connection.execute(f"DELETE FROM {table} WHERE release_id = ?", (release_id,))
        for position, artist in enumerate(release.get("artists") or []):
            artist_key = self._artist_key(connection, artist)
            connection.execute(
                """INSERT INTO discogs_release_artists
                   (release_id, position, artist_key, anv, join_text, role) VALUES (?, ?, ?, ?, ?, ?)""",
                (release_id, position, artist_key, artist.get("anv") or "", artist.get("join") or "", artist.get("role") or ""),
            )
        for position, label in enumerate(release.get("labels") or []):
            label_key = self._label_key(connection, label)
            connection.execute(
                """INSERT INTO discogs_release_labels
                   (release_id, position, label_key, catalog_number) VALUES (?, ?, ?, ?)""",
                (release_id, position, label_key, label.get("catno") or ""),
            )
        for position, fmt in enumerate(release.get("formats") or []):
            connection.execute(
                """INSERT INTO discogs_release_formats
                   (release_id, position, name, quantity, format_text) VALUES (?, ?, ?, ?, ?)""",
                (release_id, position, fmt.get("name") or "", str(fmt.get("qty") or ""), fmt.get("text") or ""),
            )
            connection.executemany(
                """INSERT INTO discogs_format_descriptions
                   (release_id, format_position, position, description) VALUES (?, ?, ?, ?)""",
                [(release_id, position, desc_position, desc)
                 for desc_position, desc in enumerate(fmt.get("descriptions") or [])],
            )
        for kind, values in (("genre", release.get("genres") or []), ("style", release.get("styles") or [])):
            connection.executemany(
                "INSERT INTO discogs_release_classifications (release_id, kind, value) VALUES (?, ?, ?)",
                [(release_id, kind, value) for value in dict.fromkeys(values)],
            )

    def _save_full_release(self, connection: sqlite3.Connection, payload: dict[str, Any], fetched_at: float) -> None:
        release_id = int(payload["id"])
        self._save_release_metadata(connection, payload, fetched_at)
        connection.execute("DELETE FROM discogs_track_credits WHERE track_key IN (SELECT track_key FROM discogs_tracks WHERE release_id=?)", (release_id,))
        connection.execute("DELETE FROM discogs_tracks WHERE release_id = ?", (release_id,))
        connection.execute("DELETE FROM discogs_release_credits WHERE release_id = ?", (release_id,))

        def insert_track_items(items: list[dict[str, Any]], parent_sequence: int | None = None) -> None:
            for item in items:
                sequence = int(connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM discogs_tracks WHERE release_id = ?", (release_id,)
                ).fetchone()[0])
                discogs_track_id = str(item.get("id") or "")
                track_key = f"{release_id}:{discogs_track_id}" if discogs_track_id else f"{release_id}:sequence:{sequence}"
                duration = str(item.get("duration") or "")
                track_type = str(item.get("type_") or item.get("type") or "track")
                connection.execute(
                    """INSERT INTO discogs_tracks
                       (track_key, release_id, sequence, discogs_track_id, position, title, duration, duration_ms, track_type, parent_sequence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (track_key, release_id, sequence, discogs_track_id or None, item.get("position") or "",
                     item.get("title") or "", duration, self._duration_milliseconds(duration), track_type, parent_sequence),
                )
                for credit_position, artist in enumerate(item.get("artists") or []):
                    artist_key = self._artist_key(connection, artist)
                    connection.execute(
                        "INSERT INTO discogs_track_credits (track_key, position, artist_key, role) VALUES (?, ?, ?, ?)",
                        (track_key, credit_position, artist_key, artist.get("role") or ""),
                    )
                insert_track_items(item.get("sub_tracks") or [], sequence)

        insert_track_items(payload.get("tracklist") or [])
        for position, artist in enumerate(payload.get("extraartists") or []):
            artist_key = self._artist_key(connection, artist)
            connection.execute(
                "INSERT INTO discogs_release_credits (release_id, position, artist_key, role) VALUES (?, ?, ?, ?)",
                (release_id, position, artist_key, artist.get("role") or ""),
            )

    def collection_snapshot(self, username: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        with self.connect() as connection:
            sync = connection.execute(
                "SELECT * FROM discogs_collection_sync WHERE username = ?", (username,)
            ).fetchone()
            if sync is None:
                return [], None
            rows = connection.execute(
                """SELECT e.instance_id, e.release_id, e.date_added, e.folder_id,
                          r.title, r.year, r.thumb, r.cover_image, r.resource_url, r.uri,
                          r.master_id, m.year AS master_year, m.artwork_url AS master_artwork_url
                   FROM discogs_collection_entries e JOIN discogs_releases r USING (release_id)
                   LEFT JOIN discogs_masters m ON m.master_id = r.master_id
                   ORDER BY r.title COLLATE NOCASE"""
            ).fetchall()
            items = [dict(row) for row in rows]
            for row in items:
                rid = row["release_id"]
                row["artist"] = ", ".join(item[0] for item in connection.execute(
                    """SELECT a.name FROM discogs_release_artists ra JOIN discogs_artists a USING (artist_key)
                       WHERE ra.release_id=? ORDER BY ra.position""", (rid,)))
                row["artist_names"] = [item[0] for item in connection.execute(
                    """SELECT a.name FROM discogs_release_artists ra JOIN discogs_artists a USING (artist_key)
                       WHERE ra.release_id=? ORDER BY ra.position""", (rid,))]
                label_rows = connection.execute(
                    """SELECT l.name, rl.catalog_number FROM discogs_release_labels rl
                       JOIN discogs_labels l USING (label_key) WHERE rl.release_id=? ORDER BY rl.position""", (rid,)
                ).fetchall()
                row["labels"] = [item["name"] for item in label_rows]
                row["catalog_numbers"] = [item["catalog_number"] for item in label_rows if item["catalog_number"]]
                row["formats"] = [item[0] for item in connection.execute(
                    "SELECT name FROM discogs_release_formats WHERE release_id=? ORDER BY position", (rid,))]
                row["genres"] = [item[0] for item in connection.execute(
                    "SELECT value FROM discogs_release_classifications WHERE release_id=? AND kind='genre' ORDER BY value COLLATE NOCASE", (rid,))]
                row["styles"] = [item[0] for item in connection.execute(
                    "SELECT value FROM discogs_release_classifications WHERE release_id=? AND kind='style' ORDER BY value COLLATE NOCASE", (rid,))]
        return items, {"synced_at": float(sync["synced_at"]), "total": int(sync["total"])}

    def random_collection_release(self, username: str) -> tuple[dict[str, Any] | None, int]:
        """Choose one owned release uniformly from the local collection cache."""
        with self.connect() as connection:
            sync = connection.execute(
                "SELECT 1 FROM discogs_collection_sync WHERE username = ?", (username,)
            ).fetchone()
            if sync is None:
                return None, 0
            rows = connection.execute(
                """SELECT DISTINCT r.release_id, r.title, r.year AS release_year,
                          r.thumb, r.cover_image AS release_artwork_url, r.uri,
                          r.resource_url, r.master_id, m.year AS master_year,
                          m.artwork_url AS master_artwork_url
                   FROM discogs_collection_entries e
                   JOIN discogs_releases r USING (release_id)
                   LEFT JOIN discogs_masters m ON m.master_id = r.master_id
                   ORDER BY r.release_id"""
            ).fetchall()
            if not rows:
                return None, 0
            selected = dict(secrets.choice(rows))
            release_id = int(selected["release_id"])
            selected["artist"] = ", ".join(row[0] for row in connection.execute(
                """SELECT a.name FROM discogs_release_artists ra
                   JOIN discogs_artists a USING (artist_key)
                   WHERE ra.release_id = ? ORDER BY ra.position""", (release_id,)
            ))
            selected["formats"] = [row[0] for row in connection.execute(
                "SELECT name FROM discogs_release_formats WHERE release_id = ? ORDER BY position", (release_id,)
            )]
            selected["date_added"] = connection.execute(
                "SELECT date_added FROM discogs_collection_entries WHERE release_id = ? ORDER BY date_added DESC LIMIT 1",
                (release_id,),
            ).fetchone()[0]
        selected["artwork_url"] = (
            selected.get("release_artwork_url") or selected.get("thumb")
            or selected.get("master_artwork_url") or ""
        )
        selected["discogs_url"] = f"https://www.discogs.com/release/{release_id}"
        return selected, len(rows)

    def collection_artwork_sample(self, username: str, limit: int | None = None) -> list[str]:
        """Return unique cached cover URLs, optionally capped for callers that need a subset."""
        with self.connect() as connection:
            sync = connection.execute(
                "SELECT 1 FROM discogs_collection_sync WHERE username = ?", (username,)
            ).fetchone()
            if sync is None:
                return []
            artwork_urls = [row[0] for row in connection.execute(
                """SELECT DISTINCT COALESCE(NULLIF(r.cover_image, ''), NULLIF(r.thumb, ''),
                                             NULLIF(m.artwork_url, '')) AS artwork_url
                   FROM discogs_collection_entries e
                   JOIN discogs_releases r USING (release_id)
                   LEFT JOIN discogs_masters m ON m.master_id = r.master_id
                   WHERE COALESCE(NULLIF(r.cover_image, ''), NULLIF(r.thumb, ''),
                                  NULLIF(m.artwork_url, '')) IS NOT NULL
                   ORDER BY artwork_url COLLATE NOCASE"""
            )]
        if limit is not None:
            limit = max(1, min(1000, int(limit)))
        if limit is not None and len(artwork_urls) > limit:
            artwork_urls = secrets.SystemRandom().sample(artwork_urls, limit)
        return artwork_urls

    def replace_collection(self, username: str, items: list[dict[str, Any]], total: int) -> float:
        now = time.time()
        with self.connect() as connection:
            connection.execute("DELETE FROM discogs_collection_entries")
            for item in items:
                release = {
                    "id": item.get("release_id"), "title": item.get("title", ""), "year": item.get("year"),
                    "artists": item.get("artists", []), "labels": item.get("raw_labels", []),
                    "formats": item.get("raw_formats", []), "genres": item.get("genres", []),
                    "styles": item.get("styles", []), "thumb": item.get("thumb", ""),
                    "cover_image": item.get("cover_image", ""), "resource_url": item.get("resource_url", ""),
                    "uri": item.get("uri", ""), "master_id": item.get("master_id"),
                }
                self._save_release_metadata(connection, release)
                connection.execute(
                    """INSERT OR REPLACE INTO discogs_collection_entries
                       (instance_id, release_id, date_added, folder_id) VALUES (?, ?, ?, ?)""",
                    (item.get("instance_id") or item.get("release_id"), item.get("release_id"),
                     item.get("date_added", ""), item.get("folder_id")),
                )
            connection.execute(
                """INSERT INTO discogs_collection_sync (username, synced_at, total) VALUES (?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET synced_at=excluded.synced_at, total=excluded.total""",
                (username, now, total),
            )
        return now

    def release_details(self, release_id: int) -> tuple[dict[str, Any], float] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, fetched_at FROM discogs_release_payloads WHERE release_id = ?", (release_id,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"]), float(row["fetched_at"])

    def collection_enrichment_queue(self, username: str) -> tuple[list[int], list[int]]:
        with self.connect() as connection:
            releases = [int(row[0]) for row in connection.execute(
                """SELECT DISTINCT r.release_id FROM discogs_collection_entries e
                   JOIN discogs_releases r USING (release_id)
                   LEFT JOIN discogs_release_payloads p USING (release_id)
                   WHERE p.release_id IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM discogs_metadata_failures f
                       WHERE f.entity_type='release' AND f.entity_id=r.release_id
                     )
                   ORDER BY r.release_id"""
            )]
            masters = [int(row[0]) for row in connection.execute(
                """SELECT DISTINCT r.master_id FROM discogs_collection_entries e
                   JOIN discogs_releases r USING (release_id)
                   LEFT JOIN discogs_masters m ON m.master_id = r.master_id
                   WHERE r.master_id > 0 AND m.master_id IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM discogs_metadata_failures f
                       WHERE f.entity_type='master' AND f.entity_id=r.master_id
                     )
                   ORDER BY r.master_id"""
            )]
        return releases, masters

    def save_metadata_not_found(self, entity_type: str, entity_id: int, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO discogs_metadata_failures
                   (entity_type, entity_id, status_code, attempted_at, message)
                   VALUES (?, ?, 404, ?, ?)
                   ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                     status_code=excluded.status_code, attempted_at=excluded.attempted_at,
                     message=excluded.message""",
                (entity_type, entity_id, time.time(), message[:500]),
            )

    def clear_metadata_failure(self, entity_type: str, entity_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM discogs_metadata_failures WHERE entity_type=? AND entity_id=?",
                (entity_type, entity_id),
            )

    def save_master(self, master_id: int, payload: dict[str, Any]) -> None:
        images = payload.get("images") or []
        image = next((item for item in images if item.get("type") == "primary"), None)
        image = image or (images[0] if images else {})
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO discogs_masters
                   (master_id, title, year, main_release_id, artwork_url, thumb_url, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(master_id) DO UPDATE SET
                     title=excluded.title, year=COALESCE(excluded.year, discogs_masters.year),
                     main_release_id=COALESCE(excluded.main_release_id, discogs_masters.main_release_id),
                     artwork_url=CASE WHEN excluded.artwork_url='' THEN discogs_masters.artwork_url ELSE excluded.artwork_url END,
                     thumb_url=CASE WHEN excluded.thumb_url='' THEN discogs_masters.thumb_url ELSE excluded.thumb_url END,
                     fetched_at=excluded.fetched_at""",
                (master_id, str(payload.get("title") or ""), payload.get("year"),
                 payload.get("main_release"), str(image.get("uri") or ""),
                 str(image.get("uri150") or ""), time.time()),
            )

    def master_details(self, master_id: int | None) -> dict[str, Any] | None:
        if not master_id:
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM discogs_masters WHERE master_id = ?", (master_id,)
            ).fetchone()
        return dict(row) if row else None

    def save_release_details(
        self, release_id: int, payload: dict[str, Any], *,
        connection: sqlite3.Connection | None = None, fetched_at: float | None = None,
    ) -> float:
        fetched_at = fetched_at or time.time()
        if connection is None:
            with self.connect() as active:
                self._store_release_details(active, release_id, payload, fetched_at)
        else:
            self._store_release_details(connection, release_id, payload, fetched_at)
        return fetched_at

    def _store_release_details(
        self, connection: sqlite3.Connection, release_id: int, payload: dict[str, Any], fetched_at: float
    ) -> None:
        self._save_full_release(connection, payload, fetched_at)
        connection.execute(
            """INSERT INTO discogs_release_payloads (release_id, fetched_at, payload_json) VALUES (?, ?, ?)
               ON CONFLICT(release_id) DO UPDATE SET fetched_at=excluded.fetched_at, payload_json=excluded.payload_json""",
            (release_id, fetched_at, json.dumps(payload, ensure_ascii=False)),
        )


class CollectionClient:
    def __init__(self) -> None:
        options = read_options()
        self.username = str(options.get("discogs_username") or "IPAIRIS").strip()
        self.token = str(options.get("discogs_token") or "").strip()
        self.refresh_schedule_enabled = bool(options.get("refresh_schedule_enabled", True))
        self.refresh_interval_seconds = max(3600, min(604800, int(options.get("refresh_interval_hours", 24)) * 3600))
        self.database = CollectionDatabase(DATABASE_PATH, LEGACY_DATABASE_PATHS)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HomeAssistantDiscogsConnector/0.7.4 (personal collection browser)",
            "Accept": "application/vnd.discogs.v2.plain+json",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Discogs token={self.token}"
        self._lock = threading.Lock()
        self._pick_lock = threading.Lock()
        self.enrichment_enabled = bool(options.get("enrich_collection_details", True))
        self._enrichment_thread: threading.Thread | None = None

    def _publish_random_pick(self, state: str, attributes: dict[str, Any]) -> None:
        supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
        if not supervisor_token:
            log("Random pick selected, but Home Assistant state was not published: SUPERVISOR_TOKEN is unavailable")
            return
        try:
            response = requests.post(
                f"http://supervisor/core/api/states/{RANDOM_PICK_ENTITY}",
                headers={"Authorization": f"Bearer {supervisor_token}", "Content-Type": "application/json"},
                json={"state": state[:255], "attributes": attributes}, timeout=10,
            )
            response.raise_for_status()
            log(f"Random pick sensor updated: status={attributes.get('status')} pick_id={attributes.get('pick_id')}")
        except Exception as exc:
            log("Could not publish random pick to Home Assistant", error=exc)

    @staticmethod
    def _dominant_artwork_color(artwork_url: str) -> str:
        """Sample a dark, readable dominant cover color from a Discogs-hosted image."""
        parsed = urlparse(artwork_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "discogs.com" or host.endswith(".discogs.com")):
            return ""
        try:
            response = requests.get(artwork_url, timeout=(2, 2), stream=True, allow_redirects=False,
                                   headers={"User-Agent": "HomeAssistantDiscogsConnector/0.7.4"})
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                if len(content) > 5 * 1024 * 1024:
                    return ""
            image = Image.open(BytesIO(content)).convert("RGB")
            image.thumbnail((48, 48))
            palette = image.quantize(colors=16).convert("RGB")
            candidates = []
            for count, color in (palette.getcolors(48 * 48) or []):
                r, g, b = color
                high, low = max(r, g, b), min(r, g, b)
                if high < 38 or low > 238 or high - low < 18:
                    continue
                candidates.append((count, r, g, b))
            if not candidates:
                return ""
            _, r, g, b = max(candidates)
            dark = [max(24, min(150, round(channel * 0.58))) for channel in (r, g, b)]
            return "#%02x%02x%02x" % tuple(dark)
        except Exception as exc:
            log("Album cover color could not be sampled; the dashboard theme color will be used", error=exc)
            return ""

    def pick_random_record(self, source: str = "dashboard") -> dict[str, Any]:
        """Select from the cached collection and publish the result as a HA sensor."""
        with self._pick_lock:
            pick_id = str(uuid.uuid4())
            picked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            base = {
                "friendly_name": "Discogs Random Record",
                "icon": "mdi:album",
                "status": "picking",
                "pick_id": pick_id,
                "picked_at": picked_at,
                "trigger_source": source[:80],
            }
            self._publish_random_pick("Picking a record", base)
            try:
                record, collection_size = self.database.random_collection_release(self.username)
                shuffle_artworks = self.database.collection_artwork_sample(self.username)
            except Exception as exc:
                failed = {**base, "status": "error", "error": f"Local collection lookup failed: {exc}"[:500]}
                self._publish_random_pick("Pick failed", failed)
                log("Random collection selection failed", error=exc)
                return failed
            if record is None:
                empty = {**base, "status": "empty", "collection_size": 0,
                         "error": "Load or refresh your Discogs collection before choosing a record."}
                self._publish_random_pick("Collection not loaded", empty)
                log(f"Random pick requested from {source}; local collection is empty or not loaded")
                return empty
            attributes = {
                **base,
                "status": "selected",
                "title": record.get("title") or "Untitled release",
                "artist": record.get("artist") or "Unknown artist",
                "album": record.get("title") or "Untitled release",
                "release_id": record.get("release_id"),
                "master_id": record.get("master_id"),
                "release_year": record.get("release_year"),
                "master_year": record.get("master_year"),
                "artwork_url": record.get("artwork_url") or "",
                "final_artwork_url": record.get("artwork_url") or "",
                "dominant_color": self._dominant_artwork_color(record.get("artwork_url") or ""),
                "release_artwork_url": record.get("release_artwork_url") or record.get("thumb") or "",
                "master_artwork_url": record.get("master_artwork_url") or "",
                "discogs_url": record.get("discogs_url") or "",
                "formats": record.get("formats") or [],
                "date_added": record.get("date_added") or "",
                "collection_size": collection_size,
                "shuffle_artworks": shuffle_artworks,
            }
            self._publish_random_pick(attributes["title"], attributes)
            log(
                f"Random collection pick selected: release_id={attributes['release_id']} "
                f"pick_id={pick_id} source={source} collection_size={collection_size}"
            )
            return attributes

    def status(self) -> dict[str, Any]:
        _items, sync = self.database.collection_snapshot(self.username)
        age = int(time.time() - sync["synced_at"]) if sync else None
        return {
            "username": self.username,
            "loaded": sync is not None,
            "count": sync["total"] if sync else 0,
            "age_seconds": age,
            "refresh_schedule_enabled": self.refresh_schedule_enabled,
            "refresh_interval_hours": self.refresh_interval_seconds // 3600,
            "token_configured": bool(self.token),
            "storage": "sqlite",
        }

    def start_collection_enrichment(self) -> None:
        if not self.enrichment_enabled or not self.token:
            return
        if self._enrichment_thread and self._enrichment_thread.is_alive():
            return
        self._enrichment_thread = threading.Thread(
            target=self._enrich_collection_metadata,
            name="discogs-collection-enrichment", daemon=True,
        )
        self._enrichment_thread.start()

    def _enrich_collection_metadata(self) -> None:
        releases, masters = self.database.collection_enrichment_queue(self.username)
        if not releases and not masters:
            log("Discogs collection metadata enrichment: nothing pending")
            return
        session = requests.Session()
        session.headers.update(self.session.headers)
        try:
            total = len(releases) + len(masters)
            complete = 0
            for entity_type, entity_ids in (("release", releases), ("master", masters)):
                for entity_id in entity_ids:
                    url = f"{API_ROOT}/{entity_type}s/{entity_id}"
                    try:
                        response = session.get(url, timeout=30)
                        response.raise_for_status()
                        payload = response.json()
                        if entity_type == "release":
                            self.database.save_release_details(entity_id, payload)
                        else:
                            self.database.save_master(entity_id, payload)
                        self.database.clear_metadata_failure(entity_type, entity_id)
                        complete += 1
                        log(f"Discogs collection metadata: {complete}/{total} releases/masters")
                    except requests.HTTPError as exc:
                        response = exc.response
                        status = response.status_code if response is not None else None
                        if status == 404:
                            self.database.save_metadata_not_found(entity_type, entity_id, str(exc))
                            log(f"Discogs {entity_type} metadata not found; skipping and continuing (id={entity_id})", error=exc)
                            complete += 1
                            time.sleep(1.1)
                            continue
                        log(f"Discogs {entity_type} metadata failed; pausing enrichment (id={entity_id})", error=exc)
                        return
                    except Exception as exc:
                        log(f"Discogs {entity_type} metadata failed; pausing enrichment (id={entity_id}, url={url})", error=exc)
                        return
                    time.sleep(1.1)
        finally:
            session.close()

    def collection(self, force: bool = False) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Add your Discogs personal access token in the app Configuration, then restart the app.")
        with self._lock:
            cached_items, sync = self.database.collection_snapshot(self.username)
            if not force and sync:
                self.start_collection_enrichment()
                return {"items": cached_items, "status": self.status()}

            items: list[dict[str, Any]] = []
            page = 1
            total_pages = 1
            total = 0
            while page <= total_pages and page <= MAX_PAGES:
                response = self.session.get(
                    f"{API_ROOT}/users/{self.username}/collection/folders/0/releases",
                    params={"page": page, "per_page": 100, "sort": "artist", "sort_order": "asc"},
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                pagination = payload.get("pagination") or {}
                total_pages = int(pagination.get("pages") or 1)
                total = int(pagination.get("items") or total)
                for entry in payload.get("releases") or []:
                    release = entry.get("basic_information") or {}
                    artists = release.get("artists") or []
                    labels = release.get("labels") or []
                    formats = release.get("formats") or []
                    artists_text = ", ".join(a.get("name", "") for a in artists if a.get("name"))
                    items.append({
                        "instance_id": entry.get("instance_id"),
                        "release_id": release.get("id"),
                        "title": str(release.get("title") or ""),
                        "artist": artists_text,
                        "year": release.get("year"),
                        "formats": [f.get("name", "") for f in formats if f.get("name")],
                        "artists": release.get("artists") or [],
                        "raw_labels": labels,
                        "raw_formats": formats,
                        "genres": release.get("genres") or [],
                        "styles": release.get("styles") or [],
                        "master_id": release.get("master_id"),
                        "labels": [l.get("name", "") for l in labels if l.get("name")],
                        "catalog_numbers": [l.get("catno", "") for l in labels if l.get("catno")],
                        "thumb": release.get("thumb") or "",
                        "cover_image": release.get("cover_image") or "",
                        "resource_url": release.get("resource_url") or "",
                        "uri": release.get("uri") or "",
                        "date_added": entry.get("date_added") or "",
                        "folder_id": entry.get("folder_id"),
                    })
                page += 1

            self.database.replace_collection(self.username, items, total or len(items))
            self.start_collection_enrichment()
            return {"items": items, "status": self.status()}

    def overview(self) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Add your Discogs personal access token in the app Configuration, then restart the app.")
        items, sync = self.database.collection_snapshot(self.username)
        if not sync:
            raise RuntimeError("Load your collection first to build its overview.")
        response = self.session.get(f"{API_ROOT}/users/{self.username}", timeout=30)
        response.raise_for_status()
        profile = response.json()

        def top_values(key: str, limit: int = 5) -> list[dict[str, Any]]:
            counts: dict[str, int] = {}
            for item in items:
                for value in set(item.get(key) or []):
                    if value:
                        counts[str(value)] = counts.get(str(value), 0) + 1
            return [{"name": name, "count": count} for name, count in sorted(
                counts.items(), key=lambda pair: (-pair[1], pair[0].casefold())
            )[:limit]]

        years = [int(item["year"]) for item in items if str(item.get("year") or "").isdigit() and int(item["year"]) > 0]
        decade_counts: dict[str, int] = {}
        for year in years:
            decade = f"{year // 10 * 10}s"
            decade_counts[decade] = decade_counts.get(decade, 0) + 1
        decade_rows = [{"name": name, "count": count} for name, count in sorted(
            decade_counts.items(), key=lambda pair: (-pair[1], pair[0])
        )]
        return {
            "profile": {key: profile.get(key) for key in ("username", "name", "location", "registered", "profile", "avatar_url")},
            "stats": {
                "record_count": len(items),
                "dated_count": len(years),
                "average_year": round(sum(years) / len(years)) if years else None,
                "oldest_year": min(years) if years else None,
                "newest_year": max(years) if years else None,
                "top_decades": decade_rows[:5],
                "top_artists": top_values("artist_names"),
                "top_genres": top_values("genres"),
                "top_styles": top_values("styles"),
                "top_formats": top_values("formats"),
                "top_labels": top_values("labels"),
                "last_synced_at": sync["synced_at"],
            },
        }

    def marketplace_stats(self, release_id: int) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Add your Discogs personal access token in the app Configuration, then restart the app.")
        response = self.session.get(
            f"{API_ROOT}/marketplace/stats/{release_id}", params={"curr_abbr": "USD"}, timeout=30
        )
        response.raise_for_status()
        payload = response.json()
        lowest = payload.get("lowest_price") or {}
        return {
            "lowest_listing": lowest.get("value") if isinstance(lowest, dict) else None,
            "currency": lowest.get("currency") if isinstance(lowest, dict) else "USD",
            "for_sale": payload.get("num_for_sale"),
            "blocked_from_sale": bool(payload.get("blocked_from_sale")),
            "fetched_at": time.time(),
        }

    def release(self, release_id: int, force: bool = False) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Add your Discogs personal access token in the app Configuration, then restart the app.")
        cached = self.database.release_details(release_id)
        if cached and not force:
            return {"release": cached[0], "master": self.database.master_details(cached[0].get("master_id")),
                    "age_seconds": int(time.time() - cached[1]), "cached": True}
        response = self.session.get(f"{API_ROOT}/releases/{release_id}", timeout=30)
        response.raise_for_status()
        payload = response.json()
        fetched_at = self.database.save_release_details(release_id, payload)
        return {"release": payload, "master": self.database.master_details(payload.get("master_id")),
                "age_seconds": int(time.time() - fetched_at), "cached": False}


CLIENT: CollectionClient | None = None


def get_client() -> CollectionClient:
    global CLIENT
    if CLIENT is None:
        CLIENT = CollectionClient()
    return CLIENT


def start_random_pick_event_listener(client: CollectionClient) -> threading.Thread | None:
    """Subscribe to a narrowly scoped HA event without exposing an app port."""
    if not os.environ.get("SUPERVISOR_TOKEN", "").strip():
        log("Random-pick Home Assistant event listener not started: SUPERVISOR_TOKEN is unavailable")
        return None

    def listen() -> None:
        backoff = 2
        while True:
            connection = None
            try:
                import websocket

                connection = websocket.create_connection("ws://supervisor/core/websocket", timeout=20)
                greeting = json.loads(connection.recv())
                if greeting.get("type") != "auth_required":
                    raise RuntimeError(f"Unexpected Home Assistant websocket greeting: {greeting.get('type')}")
                connection.send(json.dumps({
                    "type": "auth", "access_token": os.environ["SUPERVISOR_TOKEN"],
                }))
                auth_result = json.loads(connection.recv())
                if auth_result.get("type") != "auth_ok":
                    raise RuntimeError(
                        "Home Assistant rejected the app's Core API proxy token "
                        f"({auth_result.get('message', auth_result.get('type'))}); "
                        "verify homeassistant_api: true in the app manifest, then rebuild/restart the app"
                    )
                connection.send(json.dumps({
                    "id": 1, "type": "subscribe_events", "event_type": RANDOM_PICK_EVENT,
                }))
                subscription = json.loads(connection.recv())
                if not subscription.get("success"):
                    raise RuntimeError(f"Home Assistant event subscription failed: {subscription}")
                connection.settimeout(None)
                backoff = 2
                log(f"Subscribed to Home Assistant event {RANDOM_PICK_EVENT}")
                while True:
                    message = json.loads(connection.recv())
                    if message.get("type") != "event":
                        continue
                    event = message.get("event") or {}
                    if event.get("event_type") != RANDOM_PICK_EVENT:
                        continue
                    data = event.get("data") or {}
                    source = str(data.get("source") or "home_assistant_event")[:80]
                    log(f"Received random-pick request from Home Assistant event; source={source}")
                    client.pick_random_record(source=source)
            except Exception as exc:
                log(f"Home Assistant random-pick event listener disconnected; retrying in {backoff}s", error=exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

    worker = threading.Thread(target=listen, name="discogs-random-pick-events", daemon=True)
    worker.start()
    return worker


def start_scheduled_collection_refresh(client: CollectionClient) -> threading.Thread | None:
    """Refresh the collection at a configured interval; manual refresh stays available."""
    if not client.refresh_schedule_enabled or not client.token:
        return None

    def refresh_loop() -> None:
        while True:
            time.sleep(client.refresh_interval_seconds)
            try:
                result = client.collection(force=True)
                log(f"Scheduled Discogs collection refresh complete: {result['status']['count']} records")
            except Exception as exc:
                log("Scheduled Discogs collection refresh failed", error=exc)

    worker = threading.Thread(target=refresh_loop, name="discogs-scheduled-collection-refresh", daemon=True)
    worker.start()
    return worker


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discogs Collection</title>
<style>
:root{color-scheme:light dark;font:16px system-ui,sans-serif}body{margin:0;padding:24px;max-width:1500px;margin-inline:auto}
h1{margin:0 0 4px}.muted{opacity:.72}.toolbar{display:flex;gap:12px;margin:18px 0;flex-wrap:wrap}input{flex:1;min-width:220px;padding:12px;border-radius:8px;border:1px solid #888;font:inherit}
button{padding:10px 16px;border:0;border-radius:8px;font:inherit;cursor:pointer}.tabs{display:flex;gap:8px;border-bottom:1px solid #8885;margin:22px 0}.tab{background:transparent;border-radius:8px 8px 0 0;opacity:.75}.tab.active{opacity:1;border-bottom:3px solid #58a6ff}.panel{min-width:0}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}.stat,.block{padding:16px;border:1px solid #8885;border-radius:12px}.stat strong{display:block;font-size:1.55rem;margin-top:4px}.overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.list{padding-left:22px;margin-bottom:0}.list li{margin:7px 0}.table-wrap{overflow:auto;border:1px solid #8885;border-radius:12px}table.collection{width:100%;border-collapse:collapse;min-width:800px;table-layout:fixed}table.collection th,table.collection td{text-align:left;padding:10px;border-bottom:1px solid #8885;vertical-align:middle}table.collection th{position:sticky;top:0;background:Canvas;z-index:1}table.collection th:nth-child(1){width:94px}table.collection th:nth-child(2){width:28%}table.collection th:nth-child(3){width:20%}table.collection th:nth-child(4){width:100px}table.collection th:nth-child(5){width:19%}table.collection th:nth-child(6){width:16%}.cover{width:72px;height:72px;object-fit:cover;border-radius:6px;background:#7773;display:block}.title{font-weight:650}.sub{font-size:.9em;opacity:.8;margin-top:4px}.attribution{font-size:.83em;margin-top:22px}
#status{margin-top:8px}.error{color:#ff7272}.album-title{display:flex;gap:20px;align-items:flex-start;margin:18px 0}.album-cover{width:min(220px,40vw);height:min(220px,40vw);object-fit:cover;border-radius:10px;background:#7773}.tracks{width:100%;border-collapse:collapse;margin-top:12px}.tracks th,.tracks td{text-align:left;padding:9px 7px;border-bottom:1px solid #8885;vertical-align:top}.track-pos,.track-time{white-space:nowrap;opacity:.8}.release-meta{display:flex;gap:8px;flex-wrap:wrap}.badge{padding:4px 9px;border:1px solid #8886;border-radius:999px;font-size:.86em}.price-panel{padding:14px;border:1px solid #8885;border-radius:12px;margin:18px 0}.price-numbers{display:flex;gap:18px;flex-wrap:wrap}.price-numbers strong{font-size:1.2rem}
</style></head><body>
<h1>Discogs Collection</h1><div id="status" class="muted">Connect to Discogs to load your collection.</div>
<nav class="tabs" aria-label="Collection pages"><button class="tab active" data-tab="overview">Overview</button><button class="tab" data-tab="collection">Collection</button></nav>
<section id="overview-view" class="panel"><div id="overview"></div></section>
<section id="collection-view" class="panel" hidden>
<div class="toolbar"><input id="query" type="search" placeholder="Search artist, album, label, or catalog number" aria-label="Search collection"><button id="refresh">Refresh collection</button></div>
<div id="count" class="muted"></div><div class="table-wrap"><table class="collection"><thead><tr><th>Cover</th><th>Album</th><th>Artist</th><th>Year</th><th>Format</th><th>Label / catalog</th></tr></thead><tbody id="results"></tbody></table></div></section>
<section id="release-view" hidden></section>
<p class="attribution">Data provided by Discogs. This application uses Discogs’ API but is not affiliated with, sponsored or endorsed by Discogs. 'Discogs' is a trademark of Zink Media, LLC.</p>
<script>
let rows=[]; const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
function render(){const q=document.querySelector('#query').value.trim().toLocaleLowerCase();const found=rows.filter(x=>[x.artist,x.title,x.year,...x.formats,...x.labels,...x.catalog_numbers].join(' ').toLocaleLowerCase().includes(q));
document.querySelector('#results').innerHTML=found.length?found.map(x=>`<tr><td>${x.thumb?`<img class="cover" loading="lazy" src="${esc(x.thumb)}" alt="Cover for ${esc(x.title)}">`:'<div class="cover"></div>'}</td><td class="title"><a href="#release/${encodeURIComponent(x.release_id)}">${esc(x.title)}</a></td><td>${esc(x.artist)}</td><td>${esc(x.year||'—')}${x.master_year?`<div class="sub">Master ${esc(x.master_year)}</div>`:''}</td><td>${esc(x.formats.join(', ')||'—')}</td><td>${esc([...x.labels,...x.catalog_numbers].join(' · ')||'—')}</td></tr>`).join(''):'<tr><td colspan="6" class="muted">No matching records</td></tr>';
 document.querySelector('#count').textContent=`${found.length} of ${rows.length} records`;
}
function list(items){return items?.length?`<ol class="list">${items.map(x=>`<li>${esc(x.name)} <span class="muted">(${esc(x.count)})</span></li>`).join('')}</ol>`:'<p class="muted">No data available</p>';}
async function loadOverview(){const target=document.querySelector('#overview');target.innerHTML='<p class="muted">Loading profile and collection insights…</p>';try{const r=await fetch('api/overview');const data=await r.json();if(!r.ok)throw new Error(data.error||'Could not load overview');const p=data.profile||{},s=data.stats||{};target.innerHTML=`<section class="block"><h2>${esc(p.name||p.username||'Discogs profile')}</h2><p class="muted">${esc([p.location,p.registered&&('Member since '+String(p.registered).slice(0,4))].filter(Boolean).join(' · '))}</p>${p.profile?`<p>${esc(p.profile)}</p>`:''}</section><div class="stats"><div class="stat">Records<strong>${esc(s.record_count)}</strong></div><div class="stat">Average release year<strong>${esc(s.average_year||'—')}</strong><span class="muted">${esc(s.dated_count)} with a year listed</span></div><div class="stat">Release year range<strong>${esc(s.oldest_year||'—')} – ${esc(s.newest_year||'—')}</strong></div><div class="stat">Most common decade<strong>${esc(s.top_decades?.[0]?.name||'—')}</strong><span class="muted">${esc(s.top_decades?.[0]?.count||0)} records</span></div></div><div class="overview-grid"><section class="block"><h3>Most represented artists</h3>${list(s.top_artists)}</section><section class="block"><h3>Favorite genres</h3>${list(s.top_genres)}</section><section class="block"><h3>Styles</h3>${list(s.top_styles)}</section><section class="block"><h3>Formats</h3>${list(s.top_formats)}</section><section class="block"><h3>Labels</h3>${list(s.top_labels)}</section><section class="block"><h3>Collection age</h3>${list(s.top_decades)}</section></div>`;}catch(e){target.innerHTML=`<p class="error">${esc(e.message)}</p><button onclick="loadOverview()">Retry</button>`;}}
async function load(force=false){const st=document.querySelector('#status');st.className='muted';st.textContent=force?'Refreshing from Discogs…':'Loading your collection…';const button=document.querySelector('#refresh');if(button)button.disabled=true;
 try{const r=await fetch('api/collection'+(force?'?refresh=1':''));const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');rows=data.items||[];const age=Math.max(0,Math.round((data.status.age_seconds||0)/60));st.textContent=`${esc(data.status.username)} · ${rows.length} records · refreshed ${age} min ago`;render();loadOverview();}
 catch(e){st.className='error';st.textContent=e.message;}
 finally{if(button)button.disabled=false;}}
function showTab(tab){document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));document.querySelector('#overview-view').hidden=tab!=='overview';document.querySelector('#collection-view').hidden=tab!=='collection';}
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));
function backToCollection(){location.hash='';document.querySelector('#release-view').hidden=true;showTab('collection');}
async function showRelease(id){if(!/^\d+$/.test(id))return;document.querySelector('#overview-view').hidden=true;document.querySelector('#collection-view').hidden=true;const view=document.querySelector('#release-view');view.hidden=false;view.innerHTML='<button id="back">← Back to collection</button><p class="muted">Loading release details…</p>';view.querySelector('#back').onclick=backToCollection;
 try{const [r,pr]=await Promise.all([fetch('api/releases/'+encodeURIComponent(id)),fetch('api/marketplace/'+encodeURIComponent(id))]);const result=await r.json(),pricing=await pr.json();if(!r.ok)throw new Error(result.error||'Could not load release');const x=result.release||{};const tracks=x.tracklist||[];const title=x.title||'Release details';const artist=(x.artists||[]).map(a=>a.name).filter(Boolean).join(', ');const formats=(x.formats||[]).map(f=>[f.name,f.qty,f.text].filter(Boolean).join(' '));const labels=(x.labels||[]).map(l=>[l.name,l.catno].filter(Boolean).join(' · '));const credits=(x.extraartists||[]).map(a=>[a.name,a.role].filter(Boolean).join(' — '));const price=pricing.stats||{};const priceView=pricing.error?`<p class="muted">Current listing information could not be loaded: ${esc(pricing.error)}</p>`:`<p class="muted">This is the current asking price for an active listing, not the sold-price history.</p><div class="price-numbers"><span>Lowest current listing <strong>${price.lowest_listing!=null?esc(price.currency)+' '+esc(Number(price.lowest_listing).toFixed(2)):'Unavailable'}</strong></span><span>For sale <strong>${price.for_sale==null?'Unavailable':esc(price.for_sale)}</strong></span></div><p class="muted">Discogs’ API does not provide the recent sold-sales low / median / high summary here. See the release page for sales history.</p>`;
 view.innerHTML=`<button id="back">← Back to collection</button><h1>${esc(title)}</h1><div class="album-title">${x.images?.[0]?.uri?`<img class="album-cover" src="${esc(x.images[0].uri)}" alt="Album artwork">`:''}<div><div class="sub">${esc(artist)}${x.year?' · '+esc(x.year):''}</div><div class="release-meta">${[...formats,...labels,...(x.genres||[]),...(x.styles||[])].map(v=>`<span class="badge">${esc(v)}</span>`).join('')}</div><p>${esc([x.country,x.released&&('Released '+x.released)].filter(Boolean).join(' · '))}</p>${credits.length?`<details><summary>Credits</summary><p>${credits.map(esc).join('<br>')}</p></details>`:''}</div></div><section class="price-panel"><h2>Marketplace pricing</h2>${priceView}</section><h2>Tracklist</h2>${tracks.length?`<table class="tracks"><thead><tr><th>Pos.</th><th>Track</th><th>Duration</th></tr></thead><tbody>${tracks.map(t=>`<tr><td class="track-pos">${esc(t.position)}</td><td>${esc(t.title)}${t.type_==='heading'?' (side/section)':''}</td><td class="track-time">${esc(t.duration)}</td></tr>`).join('')}</tbody></table>`:'<p>No tracklist was provided for this release.</p>'}${x.notes?`<details><summary>Discogs notes</summary><p>${esc(x.notes).replace(/\n/g,'<br>')}</p></details>`:''}<p class="muted">Release details ${result.cached?'loaded from local database':'fetched from Discogs and saved locally'} · ${result.age_seconds?Math.round(result.age_seconds/60)+' min ago':''}</p><p><a href="https://www.discogs.com/release/${encodeURIComponent(id)}" target="_blank" rel="noopener">Data provided by Discogs · open release and sales history</a></p>`;view.querySelector('#back').onclick=backToCollection;
 }catch(e){view.innerHTML=`<button id="back">← Back to collection</button><p class="error">${esc(e.message)}</p>`;view.querySelector('#back').onclick=backToCollection;}}
function route(){const match=location.hash.match(/^#release\/(\d+)$/);if(match)showRelease(match[1]);else{const activeTab=document.querySelector('.tab.active')?.dataset.tab||'overview';document.querySelector('#release-view').hidden=true;showTab(activeTab);}}
document.querySelector('#query').addEventListener('input',render);document.querySelector('#refresh').addEventListener('click',()=>load(true));window.addEventListener('hashchange',route);load();route();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path)
        if path.path != "/api/random":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request_data = json.loads(self.rfile.read(length) or b"{}") if length else {}
            source = str(request_data.get("source") or "discogs_app")[:80]
            self.send_json(200, {"pick": get_client().pick_random_record(source=source)})
        except Exception as exc:
            log("Manual random-pick request failed", error=exc)
            self.send_json(500, {"error": "Could not choose a record. Check the Discogs Connector app log."})

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path)
        if path.path == "/api/status":
            self.send_json(200, get_client().status())
            return
        if path.path == "/api/collection":
            force = "refresh=1" in path.query.split("&")
            try:
                self.send_json(200, get_client().collection(force=force))
            except requests.HTTPError as exc:
                log("Discogs collection request failed", error=exc)
                self.send_json(502, {"error": self.discogs_error(exc)})
            except (requests.RequestException, ValueError) as exc:
                log("Discogs collection request failed", error=exc)
                self.send_json(502, {"error": "Could not reach Discogs. Check the app log and try again."})
            except RuntimeError as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path.path == "/api/overview":
            try:
                self.send_json(200, get_client().overview())
            except requests.HTTPError as exc:
                log("Discogs profile request failed", error=exc)
                self.send_json(502, {"error": self.discogs_error(exc)})
            except (requests.RequestException, ValueError) as exc:
                log("Discogs profile request failed", error=exc)
                self.send_json(502, {"error": "Could not load your Discogs profile. Check the app log and try again."})
            except RuntimeError as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path.path.startswith("/api/marketplace/"):
            raw_id = path.path.removeprefix("/api/marketplace/")
            if not raw_id.isdigit():
                self.send_json(400, {"error": "Release ID must be a number."})
                return
            try:
                self.send_json(200, {"stats": get_client().marketplace_stats(int(raw_id))})
            except requests.HTTPError as exc:
                log("Discogs marketplace stats request failed", error=exc)
                self.send_json(502, {"error": self.discogs_error(exc)})
            except (requests.RequestException, ValueError) as exc:
                log("Discogs marketplace stats request failed", error=exc)
                self.send_json(502, {"error": "Could not load current listing information."})
            except RuntimeError as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path.path.startswith("/api/releases/"):
            raw_id = path.path.removeprefix("/api/releases/")
            if not raw_id.isdigit():
                self.send_json(400, {"error": "Release ID must be a number."})
                return
            try:
                self.send_json(200, get_client().release(int(raw_id)))
            except requests.HTTPError as exc:
                log("Discogs release request failed", error=exc)
                self.send_json(502, {"error": self.discogs_error(exc)})
            except (requests.RequestException, ValueError) as exc:
                log("Discogs release request failed", error=exc)
                self.send_json(502, {"error": "Could not load release details. Check the app log and try again."})
            except RuntimeError as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    @staticmethod
    def discogs_error(exc: requests.HTTPError) -> str:
        status = exc.response.status_code if exc.response is not None else 502
        if status == 401 or status == 403:
            return "Discogs rejected the token. Check the token and username in app Configuration."
        if status == 404:
            return "Discogs could not find that collection or release. Check the account and release ID."
        if status == 429:
            return "Discogs rate limit reached. Wait before refreshing again."
        return f"Discogs request failed (HTTP {status})."

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        log("Discogs Connector: " + fmt % args)


if __name__ == "__main__":
    client = get_client()
    log(f"Discogs Connector listening on {PORT}; user={client.username}; token_configured={bool(client.token)}")
    start_scheduled_collection_refresh(client)
    start_random_pick_event_listener(client)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

