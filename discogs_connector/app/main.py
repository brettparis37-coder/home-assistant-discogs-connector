#!/usr/bin/env python3
"""Small ingress web app for querying a Discogs collection on demand."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

OPTIONS_PATH = Path("/data/options.json")
API_ROOT = "https://api.discogs.com"
PORT = 8099
MAX_PAGES = 100


def read_options() -> dict[str, Any]:
    try:
        return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class CollectionClient:
    def __init__(self) -> None:
        options = read_options()
        self.username = str(options.get("discogs_username") or "IPAIRIS").strip()
        self.token = str(options.get("discogs_token") or "").strip()
        self.ttl_seconds = max(900, min(18000, int(options.get("cache_ttl_minutes", 240)) * 60))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HomeAssistantDiscogsConnector/0.1.0 (personal collection browser)",
            "Accept": "application/vnd.discogs.v2.plain+json",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Discogs token={self.token}"
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] = []
        self._loaded_at = 0.0
        self._total = 0

    def status(self) -> dict[str, Any]:
        age = int(time.time() - self._loaded_at) if self._loaded_at else None
        return {
            "username": self.username,
            "loaded": bool(self._loaded_at and age is not None and age < 6 * 3600),
            "count": self._total if age is not None and age < 6 * 3600 else 0,
            "age_seconds": age,
            "cache_ttl_seconds": self.ttl_seconds,
            "token_configured": bool(self.token),
        }

    def collection(self, force: bool = False) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Add your Discogs personal access token in the app Configuration, then restart the app.")
        with self._lock:
            now = time.time()
            age = now - self._loaded_at if self._loaded_at else None
            if not force and self._loaded_at and age is not None and age < self.ttl_seconds:
                return {"items": self._items, "status": self.status()}

            items: list[dict[str, Any]] = []
            page = 1
            total_pages = 1
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

            # In-memory only. No collection or image data is written under /data.
            self._items = items
            self._total = int((payload.get("pagination") or {}).get("items") or len(items))
            self._loaded_at = time.time()
            return {"items": self._items, "status": self.status()}


CLIENT = CollectionClient()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discogs Collection</title>
<style>
:root{color-scheme:light dark;font:16px system-ui,sans-serif}body{margin:0;padding:24px;max-width:1200px;margin-inline:auto}
h1{margin:0 0 4px}.muted{opacity:.72}.toolbar{display:flex;gap:12px;margin:20px 0;flex-wrap:wrap}input{flex:1;min-width:220px;padding:12px;border-radius:8px;border:1px solid #888;font:inherit}
button{padding:10px 16px;border:0;border-radius:8px;font:inherit;cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{display:flex;gap:12px;padding:12px;border:1px solid #8885;border-radius:12px;min-height:112px}.cover{width:92px;height:92px;object-fit:cover;border-radius:6px;background:#7773;flex:none}.details{min-width:0}.title{font-weight:650}.sub{font-size:.9em;opacity:.8;margin-top:4px}.attribution{font-size:.83em;margin-top:18px}
#status{margin-top:8px}.error{color:#ff7272}
</style></head><body>
<h1>Discogs Collection</h1><div id="status" class="muted">Connect to Discogs to load your collection.</div>
<div class="toolbar"><input id="query" type="search" placeholder="Search artist, album, label, or catalog number" aria-label="Search collection"><button id="refresh">Refresh collection</button></div>
<main id="results" class="grid"></main>
<p class="attribution">Data provided by Discogs. This application uses Discogs’ API but is not affiliated with, sponsored or endorsed by Discogs. 'Discogs' is a trademark of Zink Media, LLC. Each result links to its Discogs release page.</p>
<script>
let rows=[]; const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
function render(){const q=document.querySelector('#query').value.trim().toLocaleLowerCase();const found=rows.filter(x=>[x.artist,x.title,x.year,...x.formats,...x.labels,...x.catalog_numbers].join(' ').toLocaleLowerCase().includes(q));
 document.querySelector('#results').innerHTML=found.map(x=>`<article class="card">${x.thumb?`<img class="cover" loading="lazy" src="${esc(x.thumb)}" alt="">`:'<div class="cover"></div>'}<div class="details"><div class="title">${esc(x.title)}</div><div class="sub">${esc(x.artist)}${x.year?' · '+esc(x.year):''}</div><div class="sub">${esc([...x.formats,...x.labels].join(' · '))}</div><div class="sub">${esc(x.catalog_numbers.join(', '))}</div><div class="sub"><a href="https://www.discogs.com${esc(x.uri||'/release/'+x.release_id)}" target="_blank" rel="noopener">Data provided by Discogs</a></div></div></article>`).join('');
 document.querySelector('#count').textContent=`${found.length} shown`;
}
async function load(force=false){const st=document.querySelector('#status');st.className='muted';st.textContent=force?'Refreshing from Discogs…':'Loading your collection…';document.querySelector('#refresh').disabled=true;
 try{const r=await fetch('api/collection'+(force?'?refresh=1':''));const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');rows=data.items||[];const age=Math.max(0,Math.round((data.status.age_seconds||0)/60));st.innerHTML=`<span id="count"></span> · ${esc(data.status.username)} · fetched ${age} min ago (memory cache)`;render();}
 catch(e){st.className='error';st.textContent=e.message;}
 finally{document.querySelector('#refresh').disabled=false;}}
document.querySelector('#query').addEventListener('input',render);document.querySelector('#refresh').addEventListener('click',()=>load(true));load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path)
        if path.path == "/api/status":
            self.send_json(200, CLIENT.status())
            return
        if path.path == "/api/collection":
            force = "refresh=1" in path.query.split("&")
            try:
                self.send_json(200, CLIENT.collection(force=force))
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 502
                if status == 401 or status == 403:
                    message = "Discogs rejected the token. Check the token and username in app Configuration."
                elif status == 404:
                    message = "Discogs could not find that collection. Check the username in app Configuration."
                elif status == 429:
                    message = "Discogs rate limit reached. Wait before refreshing again."
                else:
                    message = f"Discogs request failed (HTTP {status})."
                self.send_json(502, {"error": message})
            except (requests.RequestException, ValueError) as exc:
                print(f"Discogs request failed: {exc}", flush=True)
                self.send_json(502, {"error": "Could not reach Discogs. Check the app log and try again."})
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
    print(f"Discogs Connector listening on {PORT}; user={CLIENT.username}; token_configured={bool(CLIENT.token)}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
