"""Unit tests for pagination, normalized collection rows, and memory caching."""

from __future__ import annotations

import importlib.util
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

    def get(self, url: str, *, params: dict, timeout: int) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        page = params["page"]
        row = {
            "instance_id": page,
            "date_added": "2026-09-01T00:00:00-00:00",
            "basic_information": {
                "id": 100 + page,
                "title": f"Album {page}",
                "year": 1970 + page,
                "artists": [{"name": f"Artist {page}"}],
                "labels": [{"name": "Example Label", "catno": f"CAT-{page}"}],
                "formats": [{"name": "Vinyl"}],
                "thumb": "https://img.discogs.com/thumb.jpg",
                "uri": f"/release/{100 + page}-Album-{page}",
            },
        }
        return FakeResponse({
            "pagination": {"pages": 2, "items": 2},
            "releases": [row] if page <= 2 else [],
        })


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
        self.options_path = Path(self.temp.name) / "options.json"
        self.options_path.write_text(
            '{"discogs_username":"IPAIRIS","discogs_token":"test-only","cache_ttl_minutes":240}',
            encoding="utf-8",
        )
        self.module = load_module()
        self.client_patch = patch.object(self.module, "OPTIONS_PATH", self.options_path)
        self.client_patch.start()
        self.client = self.module.CollectionClient()

    def tearDown(self) -> None:
        self.client_patch.stop()
        self.temp.cleanup()

    def test_fetches_all_pages_and_normalizes_fields(self) -> None:
        result = self.client.collection()
        self.assertEqual(len(self.client.session.calls), 2)
        self.assertEqual(result["status"]["count"], 2)
        self.assertEqual(result["items"][0]["artist"], "Artist 1")
        self.assertEqual(result["items"][0]["catalog_numbers"], ["CAT-1"])
        self.assertEqual(result["items"][1]["release_id"], 102)

    def test_reuses_fresh_memory_cache_unless_forced(self) -> None:
        self.client.collection()
        cached = self.client.collection()
        self.assertEqual(len(self.client.session.calls), 2)
        self.assertEqual(len(cached["items"]), 2)
        self.client.collection(force=True)
        self.assertEqual(len(self.client.session.calls), 4)

    def test_requires_token(self) -> None:
        self.client.token = ""
        with self.assertRaisesRegex(RuntimeError, "personal access token"):
            self.client.collection()


if __name__ == "__main__":
    unittest.main()
