"""Tests for Discogs pagination, shared storage, normalization, and migrations."""

from __future__ import annotations

import importlib.util
import gc
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict | None = None, timeout: int) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if "/marketplace/stats/" in url:
            return FakeResponse({
                "lowest_price": {"value": 12.34, "currency": "USD"},
                "num_for_sale": 8, "blocked_from_sale": False,
            })
        if "/users/" in url and "/collection/" not in url:
            return FakeResponse({
                "username": "IPAIRIS", "name": "Record Collector", "location": "California",
                "registered": "2010-01-01 00:00:00", "profile": "A Discogs profile",
            })
        if "/releases/" in url:
            release_id = int(url.rsplit("/", 1)[1])
            return FakeResponse({
                "id": release_id, "title": "Album detail", "year": 1971,
                "artists": [{"id": 11, "name": "Artist Detail"}],
                "labels": [{"id": 21, "name": "Detail Label", "catno": "DL-1"}],
                "formats": [{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Album"]}],
                "genres": ["Rock"], "styles": ["Prog Rock"],
                "extraartists": [{"id": 12, "name": "Guest", "role": "Vocals"}],
                "tracklist": [
                    {"id": "t1", "position": "A1", "title": "Opening Track", "duration": "3:21",
                     "artists": [{"id": 13, "name": "Track Guest", "role": "guitar"}]},
                    {"id": "t2", "position": "A2", "title": "Second Track", "duration": "4:02"},
                ],
            })
        assert params is not None
        page = params["page"]
        row = {
            "instance_id": 500 + page,
            "date_added": "2026-09-01T00:00:00-00:00",
            "basic_information": {
                "id": 100 + page, "master_id": 900 + page,
                "title": f"Album {page}", "year": 1970 + page,
                "artists": [{"id": 30 + page, "name": f"Artist {page}", "anv": "A. Name", "join": "&"}],
                "labels": [{"id": 40, "name": "Example Label", "catno": f"CAT-{page}"}],
                "formats": [{"name": "Vinyl", "qty": "1", "descriptions": ["LP"]}],
                "genres": ["Rock"], "styles": ["Alternative Rock"],
                "thumb": "https://img.discogs.com/thumb.jpg",
                "cover_image": "https://img.discogs.com/cover.jpg",
                "resource_url": f"https://api.discogs.com/releases/{100 + page}",
                "uri": f"/release/{100 + page}-Album-{page}",
            },
        }
        return FakeResponse({"pagination": {"pages": 2, "items": 2}, "releases": [row] if page <= 2 else []})


def load_module():
    fake_requests = types.ModuleType("requests")
    fake_requests.Session = FakeSession
    fake_requests.HTTPError = type("HTTPError", (Exception,), {})
    fake_requests.RequestException = type("RequestException", (Exception,), {})
    previous = sys.modules.get("requests")
    sys.modules["requests"] = fake_requests
    module_path = Path(__file__).parents[1] / "discogs_connector" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("discogs_connector_main_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    if previous is not None:
        sys.modules["requests"] = previous
    else:
        del sys.modules["requests"]
    return module


class CollectionClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.options_path = root / "options.json"
        self.options_path.write_text(
            '{"discogs_username":"IPAIRIS","discogs_token":"test-only","cache_ttl_minutes":240}',
            encoding="utf-8",
        )
        self.module = load_module()
        self.patches = [
            patch.object(self.module, "OPTIONS_PATH", self.options_path),
            patch.object(self.module, "DATABASE_PATH", root / "home_apps.sqlite3"),
            patch.object(self.module, "LEGACY_DATABASE_PATHS", (root / "legacy.sqlite3",)),
        ]
        for item in self.patches:
            item.start()
        self.database_path = root / "home_apps.sqlite3"
        self.legacy_path = root / "legacy.sqlite3"
        self.client = self.module.CollectionClient()

    def tearDown(self) -> None:
        for attr in ("client",):
            if hasattr(self, attr):
                db = getattr(self, attr).database
                try:
                    with db.connect() as connection:
                        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                setattr(getattr(self, attr), "database", None)
        for item in reversed(self.patches):
            item.stop()
        gc.collect()
        self.temp.cleanup()

    def test_fetches_all_pages_and_normalizes_fields(self) -> None:
        result = self.client.collection()
        self.assertEqual(len(self.client.session.calls), 2)
        self.assertEqual(result["status"]["count"], 2)
        self.assertEqual(result["items"][0]["artist"], "Artist 1")
        self.assertEqual(result["items"][0]["catalog_numbers"], ["CAT-1"])
        with sqlite3.connect(self.database_path) as db:
            self.assertEqual(db.execute("SELECT title FROM discogs_releases WHERE release_id=101").fetchone()[0], "Album 1")
            self.assertEqual(db.execute("SELECT name FROM discogs_artists WHERE discogs_artist_id=31").fetchone()[0], "Artist 1")
            self.assertEqual(db.execute("SELECT catalog_number FROM discogs_release_labels WHERE release_id=101").fetchone()[0], "CAT-1")
            self.assertEqual(db.execute("SELECT value FROM discogs_release_classifications WHERE release_id=101 AND kind='style'").fetchone()[0], "Alternative Rock")
            self.assertEqual(db.execute("SELECT version FROM app_schema_versions WHERE app_id='discogs_connector'").fetchone()[0], 2)
        with sqlite3.connect(self.database_path) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.client.database = None

    def test_reuses_fresh_sqlite_cache_unless_forced(self) -> None:
        self.client.collection()
        cached = self.client.collection()
        self.assertEqual(len(self.client.session.calls), 2)
        self.assertEqual(len(cached["items"]), 2)
        self.client.collection(force=True)
        self.assertEqual(len(self.client.session.calls), 4)

    def test_collection_cache_survives_client_restart(self) -> None:
        self.client.collection()
        restarted = self.module.CollectionClient()
        restored = restarted.collection()
        self.assertEqual(restored["items"][0]["title"], "Album 1")
        self.assertEqual(restarted.session.calls, [])

    def test_release_details_are_normalized_and_reused(self) -> None:
        details = self.client.release(101)
        self.assertEqual(details["release"]["tracklist"][0]["title"], "Opening Track")
        self.assertFalse(details["cached"])
        with sqlite3.connect(self.database_path) as db:
            self.assertEqual(db.execute("SELECT duration_ms FROM discogs_tracks WHERE release_id=101 AND sequence=1").fetchone()[0], 201000)
            self.assertEqual(db.execute("SELECT value FROM discogs_release_classifications WHERE release_id=101 AND kind='genre'").fetchone()[0], "Rock")
            self.assertEqual(db.execute("SELECT role FROM discogs_release_credits WHERE release_id=101").fetchone()[0], "Vocals")
            self.assertEqual(db.execute("SELECT role FROM discogs_track_credits WHERE track_key='101:t1'").fetchone()[0], "guitar")
            self.assertIsNotNone(db.execute("SELECT payload_json FROM discogs_release_payloads WHERE release_id=101").fetchone())
        with sqlite3.connect(self.database_path) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        restarted = self.module.CollectionClient()
        cached = restarted.release(101)
        self.assertTrue(cached["cached"])
        self.assertEqual(restarted.session.calls, [])

    def test_overview_builds_collection_insights_and_loads_profile(self) -> None:
        self.client.collection()
        result = self.client.overview()
        self.assertEqual(result["profile"]["username"], "IPAIRIS")
        self.assertEqual(result["stats"]["record_count"], 2)
        self.assertEqual(result["stats"]["average_year"], 1972)
        self.assertEqual(result["stats"]["top_artists"][0], {"name": "Artist 1", "count": 1})
        self.assertEqual(self.client.session.calls[-1]["url"], "https://api.discogs.com/users/IPAIRIS")

    def test_marketplace_stats_are_returned_without_persisting_them(self) -> None:
        result = self.client.marketplace_stats(101)
        self.assertEqual(result["lowest_listing"], 12.34)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["for_sale"], 8)
        self.assertEqual(self.client.session.calls[-1]["params"], {"curr_abbr": "USD"})

    def test_migrates_legacy_file_and_preserves_other_shared_tables(self) -> None:
        target_path = Path(self.temp.name) / "fresh_shared.sqlite3"
        with sqlite3.connect(target_path) as db:
            db.execute("CREATE TABLE unrelated_app_data (id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO unrelated_app_data(value) VALUES ('keep')")
            db.execute("PRAGMA user_version=77")
        with sqlite3.connect(self.legacy_path) as db:
            db.executescript("""
                CREATE TABLE collection_sync (username TEXT PRIMARY KEY, synced_at REAL, total INTEGER);
                CREATE TABLE collection_entries (
                    instance_id INTEGER PRIMARY KEY, release_id INTEGER NOT NULL, title TEXT NOT NULL,
                    artist TEXT NOT NULL, year INTEGER, formats_json TEXT, labels_json TEXT,
                    catalog_numbers_json TEXT, thumb TEXT, cover_image TEXT, resource_url TEXT,
                    uri TEXT, date_added TEXT, folder_id INTEGER
                );
                CREATE TABLE release_details (release_id INTEGER PRIMARY KEY, fetched_at REAL, payload_json TEXT);
                INSERT INTO collection_sync VALUES ('IPAIRIS', 123.0, 1);
                INSERT INTO collection_entries VALUES (501, 501, 'Kept Album', 'Kept Artist', 1977,
                    '["Vinyl"]', '["Label"]', '["OLD-1"]', '', '', '', '/release/501', '2026-01-01', 0);
                INSERT INTO release_details VALUES (501, 124.0,
                    '{"id":501,"title":"Kept Album","artists":[{"name":"Kept Artist"}],"tracklist":[{"position":"A1","title":"Kept Track","duration":"2:00"}]}');
            """)
        with sqlite3.connect(self.legacy_path) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.module.CollectionDatabase(target_path, (self.legacy_path,))
        with sqlite3.connect(target_path) as db:
            self.assertEqual(db.execute("SELECT value FROM unrelated_app_data").fetchone()[0], "keep")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 77)
            self.assertEqual(db.execute("SELECT title FROM discogs_releases WHERE release_id=501").fetchone()[0], "Kept Album")
            self.assertEqual(db.execute("SELECT title FROM discogs_tracks WHERE release_id=501").fetchone()[0], "Kept Track")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM discogs_collection_entries").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM discogs_release_payloads").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='collection_entries'").fetchone()[0], 0)
        with sqlite3.connect(self.legacy_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collection_entries").fetchone()[0], 1)

    def test_requires_token(self) -> None:
        self.client.token = ""
        with self.assertRaisesRegex(RuntimeError, "personal access token"):
            self.client.collection()


if __name__ == "__main__":
    unittest.main()

