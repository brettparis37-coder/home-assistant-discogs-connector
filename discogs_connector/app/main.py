#!/usr/bin/env python3
"""Home Assistant ingress app for a persistent Discogs collection browser."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

OPTIONS_PATH = Path("/data/options.json")
DATABASE_PATH = Path("/data/discogs.sqlite3")
API_ROOT = "https://api.discogs.com"
PORT = 8099
MAX_PAGES = 100
MAX_CACHE_AGE_SECONDS = 6 * 3600


def read_options() -> dict[str, Any]:
    try:
        return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class CollectionDatabase:
    """Small, versioned SQLite store for collection snapshots and release detail."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > 1:
                raise RuntimeError(f"Database version {version} is newer than this app supports.")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS collection_sync (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    username TEXT NOT NULL,
                    synced_at REAL NOT NULL,
                    total INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_entries (
                    instance_id INTEGER PRIMARY KEY,
                    release_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    year INTEGER,
                    formats_json TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    catalog_numbers_json TEXT NOT NULL,
                    thumb TEXT NOT NULL,
                    cover_image TEXT NOT NULL,
                    resource_url TEXT NOT NULL,
                    uri TEXT NOT NULL,
                    date_added TEXT NOT NULL,
                    folder_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_collection_release_id
                    ON collection_entries(release_id);
                CREATE TABLE IF NOT EXISTS release_details (
                    release_id INTEGER PRIMARY KEY,
                    fetched_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            if version < 1:
                connection.execute("PRAGMA user_version = 1")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def collection_snapshot(self, username: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        with self.connect() as connection:
            sync = connection.execute("SELECT * FROM collection_sync WHERE id = 1").fetchone()
            if sync is None or sync["username"].casefold() != username.casefold():
                return [], None
            entries = connection.execute(
                "SELECT * FROM collection_entries ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE"
            ).fetchall()
        items = []
        for entry in entries:
            row = dict(entry)
            for field in ("formats", "labels", "catalog_numbers"):
                row[field] = json.loads(row.pop(f"{field}_json"))
            items.append(row)
        return items, {"synced_at": float(sync["synced_at"]), "total": int(sync["total"])}

    def replace_collection(self, username: str, items: list[dict[str, Any]], total: int) -> float:
        now = time.time()
        with self.connect() as connection:
            connection.execute("DELETE FROM collection_entries")
            connection.executemany(
                """INSERT INTO collection_entries (
                    instance_id, release_id, title, artist, year, formats_json, labels_json,
                    catalog_numbers_json, thumb, cover_image, resource_url, uri, date_added, folder_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(
                    item.get("instance_id") or item.get("release_id"), item.get("release_id"),
                    item.get("title", ""), item.get("artist", ""), item.get("year"),
                    json.dumps(item.get("formats", [])), json.dumps(item.get("labels", [])),
                    json.dumps(item.get("catalog_numbers", [])), item.get("thumb", ""),
                    item.get("cover_image", ""), item.get("resource_url", ""), item.get("uri", ""),
                    item.get("date_added", ""), item.get("folder_id"),
                ) for item in items],
            )
            connection.execute(
                """INSERT INTO collection_sync (id, username, synced_at, total) VALUES (1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET username=excluded.username,
                     synced_at=excluded.synced_at, total=excluded.total""",
                (username, now, total),
            )
        return now

    def release_details(self, release_id: int) -> tuple[dict[str, Any], float] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, fetched_at FROM release_details WHERE release_id = ?",
                (release_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"]), float(row["fetched_at"])

    def save_release_details(self, release_id: int, payload: dict[str, Any]) -> float:
        fetched_at = time.time()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO release_details (release_id, fetched_at, payload_json) VALUES (?, ?, ?)
                   ON CONFLICT(release_id) DO UPDATE SET fetched_at=excluded.fetched_at,
                     payload_json=excluded.payload_json""",
                (release_id, fetched_at, json.dumps(payload, ensure_ascii=False)),
            )
        return fetched_at


class CollectionClient:
    def __init__(self) -> None:
        options = read_options()
        self.username = str(options.get("discogs_username") or "IPAIRIS").strip()
        self.token = str(options.get("discogs_token") or "").strip()
        self.ttl_seconds = max(900, min(18000, int(options.get("cache_ttl_minutes", 240)) * 60))
        self.database = CollectionDatabase(DATABASE_PATH)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HomeAssistantDiscogsConnector/0.2.0 (personal collection browser)",
            "Accept": "application/vnd.discogs.v2.plain+json",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Discogs token={self.token}"
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        _items, sync = self.database.collection_snapshot(self.username)
        age = int(time.time() - sync["synced_at"]) if sync else None
        fresh_enough_to_show = age is not None and age < MAX_CACHE_AGE_SECONDS
        return {
            "username": self.username,
            "loaded": bool(fresh_enough_to_show),
            "count": sync["total"] if fresh_enough_to_show else 0,
            "age_seconds": age,
            "cache_ttl_seconds": self.ttl_seconds,
            "token_configured": bool(self.token),
            "storage": "sqlite",
        }

    def collection(self, force: bool = False) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Add your Discogs personal access token in the app Configuration, then restart the app.")
        with self._lock:
            cached_items, sync = self.database.collection_snapshot(self.username)
            age = time.time() - sync["synced_at"] if sync else None
            if not force and sync and age is not None and age < self.ttl_seconds:
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
            return {"items": items, "status": self.status()}

    def release(self, release_id: int, force: bool = False) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Add your Discogs personal access token in the app Configuration, then restart the app.")
        cached = self.database.release_details(release_id)
        if cached and not force and time.time() - cached[1] < self.ttl_seconds:
            return {"release": cached[0], "age_seconds": int(time.time() - cached[1]), "cached": True}
        response = self.session.get(f"{API_ROOT}/releases/{release_id}", timeout=30)
        response.raise_for_status()
        payload = response.json()
        fetched_at = self.database.save_release_details(release_id, payload)
        return {"release": payload, "age_seconds": int(time.time() - fetched_at), "cached": False}


CLIENT: CollectionClient | None = None


def get_client() -> CollectionClient:
    global CLIENT
    if CLIENT is None:
        CLIENT = CollectionClient()
    return CLIENT


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discogs Collection</title>
<style>
:root{color-scheme:light dark;font:16px system-ui,sans-serif}body{margin:0;padding:24px;max-width:1200px;margin-inline:auto}
h1{margin:0 0 4px}.muted{opacity:.72}.toolbar{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}input{flex:1;min-width:220px;padding:12px;border-radius:8px;border:1px solid #888;font:inherit}
button{padding:10px 16px;border:0;border-radius:8px;font:inherit;cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{display:flex;gap:12px;padding:12px;border:1px solid #8885;border-radius:12px;min-height:112px}.cover{width:92px;height:92px;object-fit:cover;border-radius:6px;background:#7773;flex:none}.details{min-width:0}.title{font-weight:650}.sub{font-size:.9em;opacity:.8;margin-top:4px}.attribution{font-size:.83em;margin-top:18px}
#status{margin-top:8px}.error{color:#ff7272}.album-title{display:flex;gap:20px;align-items:flex-start;margin:18px 0}.album-cover{width:min(220px,40vw);height:min(220px,40vw);object-fit:cover;border-radius:10px;background:#7773}.tracks{width:100%;border-collapse:collapse;margin-top:12px}.tracks th,.tracks td{text-align:left;padding:9px 7px;border-bottom:1px solid #8885;vertical-align:top}.track-pos,.track-time{white-space:nowrap;opacity:.8}.release-meta{display:flex;gap:8px;flex-wrap:wrap}.badge{padding:4px 9px;border:1px solid #8886;border-radius:999px;font-size:.86em}
</style></head><body>
<section id="collection-view"><h1>Discogs Collection</h1><div id="status" class="muted">Connect to Discogs to load your collection.</div>
<div class="toolbar"><input id="query" type="search" placeholder="Search artist, album, label, or catalog number" aria-label="Search collection"><button id="refresh">Refresh collection</button></div>
<main id="results" class="grid"></main></section>
<section id="release-view" hidden></section>
<p class="attribution">Data provided by Discogs. This application uses Discogs’ API but is not affiliated with, sponsored or endorsed by Discogs. 'Discogs' is a trademark of Zink Media, LLC. Each result links to its Discogs release page.</p>
<script>
let rows=[]; const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
function render(){const q=document.querySelector('#query').value.trim().toLocaleLowerCase();const found=rows.filter(x=>[x.artist,x.title,x.year,...x.formats,...x.labels,...x.catalog_numbers].join(' ').toLocaleLowerCase().includes(q));
 document.querySelector('#results').innerHTML=found.map(x=>`<article class="card">${x.thumb?`<img class="cover" loading="lazy" src="${esc(x.thumb)}" alt="">`:'<div class="cover"></div>'}<div class="details"><div class="title"><a href="#release/${encodeURIComponent(x.release_id)}">${esc(x.title)}</a></div><div class="sub">${esc(x.artist)}${x.year?' · '+esc(x.year):''}</div><div class="sub">${esc([...x.formats,...x.labels].join(' · '))}</div><div class="sub">${esc(x.catalog_numbers.join(', '))}</div><div class="sub"><a href="https://www.discogs.com${esc(x.uri||'/release/'+x.release_id)}" target="_blank" rel="noopener">Data provided by Discogs</a></div></div></article>`).join('');
 const count=document.querySelector('#count');if(count)count.textContent=`${found.length} shown`;
}
async function load(force=false){const st=document.querySelector('#status');st.className='muted';st.textContent=force?'Refreshing from Discogs…':'Loading your collection…';document.querySelector('#refresh').disabled=true;
 try{const r=await fetch('api/collection'+(force?'?refresh=1':''));const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');rows=data.items||[];const age=Math.max(0,Math.round((data.status.age_seconds||0)/60));st.innerHTML=`<span id="count"></span> · ${esc(data.status.username)} · fetched ${age} min ago (saved locally)`;render();}
 catch(e){st.className='error';st.textContent=e.message;}
 finally{document.querySelector('#refresh').disabled=false;}}
function backToCollection(){location.hash='';document.querySelector('#release-view').hidden=true;document.querySelector('#collection-view').hidden=false;}
async function showRelease(id){if(!/^\d+$/.test(id))return;document.querySelector('#collection-view').hidden=true;const view=document.querySelector('#release-view');view.hidden=false;view.innerHTML='<button id="back">← Back to collection</button><p class="muted">Loading release details…</p>';view.querySelector('#back').onclick=backToCollection;
 try{const r=await fetch('api/releases/'+encodeURIComponent(id));const result=await r.json();if(!r.ok)throw new Error(result.error||'Could not load release');const x=result.release||{};const tracks=x.tracklist||[];const title=x.title||'Release details';const artist=(x.artists||[]).map(a=>a.name).filter(Boolean).join(', ');const formats=(x.formats||[]).map(f=>[f.name,f.qty,f.text].filter(Boolean).join(' '));const labels=(x.labels||[]).map(l=>[l.name,l.catno].filter(Boolean).join(' · '));const credits=(x.extraartists||[]).map(a=>[a.name,a.role].filter(Boolean).join(' — '));
 view.innerHTML=`<button id="back">← Back to collection</button><h1>${esc(title)}</h1><div class="album-title">${x.images?.[0]?.uri?`<img class="album-cover" src="${esc(x.images[0].uri)}" alt="Album artwork">`:''}<div><div class="sub">${esc(artist)}${x.year?' · '+esc(x.year):''}</div><div class="release-meta">${[...formats,...labels,...(x.genres||[]),...(x.styles||[])].map(v=>`<span class="badge">${esc(v)}</span>`).join('')}</div><p>${esc([x.country,x.released&&('Released '+x.released)].filter(Boolean).join(' · '))}</p>${credits.length?`<details><summary>Credits</summary><p>${credits.map(esc).join('<br>')}</p></details>`:''}</div></div><h2>Tracklist</h2>${tracks.length?`<table class="tracks"><thead><tr><th>Pos.</th><th>Track</th><th>Duration</th></tr></thead><tbody>${tracks.map(t=>`<tr><td class="track-pos">${esc(t.position)}</td><td>${esc(t.title)}${t.type_==='heading'?' (side/section)':''}</td><td class="track-time">${esc(t.duration)}</td></tr>`).join('')}</tbody></table>`:'<p>No tracklist was provided for this release.</p>'}${x.notes?`<details><summary>Discogs notes</summary><p>${esc(x.notes).replace(/\n/g,'<br>')}</p></details>`:''}<p class="muted">Release details ${result.cached?'loaded from local database':'fetched from Discogs and saved locally'} · ${result.age_seconds?Math.round(result.age_seconds/60)+' min ago':''}</p><p><a href="https://www.discogs.com/release/${encodeURIComponent(id)}" target="_blank" rel="noopener">Data provided by Discogs</a></p>`;view.querySelector('#back').onclick=backToCollection;
 }catch(e){view.innerHTML=`<button id="back">← Back to collection</button><p class="error">${esc(e.message)}</p>`;view.querySelector('#back').onclick=backToCollection;}}
function route(){const match=location.hash.match(/^#release\/(\d+)$/);if(match)showRelease(match[1]);else backToCollection();}
document.querySelector('#query').addEventListener('input',render);document.querySelector('#refresh').addEventListener('click',()=>load(true));window.addEventListener('hashchange',route);load();route();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
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
                self.send_json(502, {"error": self.discogs_error(exc)})
            except (requests.RequestException, ValueError) as exc:
                print(f"Discogs collection request failed: {exc}", flush=True)
                self.send_json(502, {"error": "Could not reach Discogs. Check the app log and try again."})
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
                self.send_json(502, {"error": self.discogs_error(exc)})
            except (requests.RequestException, ValueError) as exc:
                print(f"Discogs release request failed: {exc}", flush=True)
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
        print("Discogs Connector: " + fmt % args, flush=True)


if __name__ == "__main__":
    client = get_client()
    print(f"Discogs Connector listening on {PORT}; user={client.username}; token_configured={bool(client.token)}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

