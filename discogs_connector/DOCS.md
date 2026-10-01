# Home Assistant Discogs Connector

This Home Assistant app provides an ingress collection browser for the Discogs account configured in its options (default username: `IPAIRIS`).

## Shared database and schema

The app stores its data in `/share/home_apps.sqlite3`, a shared custom-app database. It is separate from Home Assistant's recorder database. The tables owned by this app use the `discogs_` prefix; migration bookkeeping is in `app_schema_versions`, keyed by app ID. This lets other custom apps add their own namespaced tables to the same SQLite file without sharing ownership of schema changes. Each app must manage its own schema version and migrations.

The schema is relational for searching and joins:

- `discogs_releases` stores one row per Discogs release.
- `discogs_collection_entries` stores your collection instances and folder/date-added fields.
- `discogs_artists`, `discogs_release_artists`, `discogs_labels`, and `discogs_release_labels` store reusable artist/label records and release relationships.
- `discogs_release_formats`, `discogs_format_descriptions`, and `discogs_release_classifications` store formats, genres, and styles.
- `discogs_tracks`, `discogs_track_credits`, and `discogs_release_credits` store cached tracklists and credits for releases in your collection.
- `discogs_track_fact_sets` stores per-track fact-generation status and snapshots; `discogs_track_facts` stores up to five ordered facts with source references. The tables can be populated through the optional track-facts MCP endpoint described below. They intentionally do not cascade-delete when Discogs refreshes a tracklist.
- `discogs_masters` stores each master release's year and primary artwork URL; release-specific year and artwork remain on `discogs_releases`.
- `discogs_release_payloads` retains the API response for cache reuse and fields not yet represented as columns. Normalized tables are the queryable representation for common lookups.
- `discogs_collection_sync` records the latest collection refresh time and item count.

Schema migrations do not use SQLite's file-global `PRAGMA user_version`, so one app will not overwrite another app's version marker. SQLite Web can view the shared file by setting its **Database** option to `/share/home_apps.sqlite3`; its Home Assistant app configuration provides one database path at a time.

## Upgrade and data migration

On first startup of version 0.3.0, the app preserves and imports existing Discogs data from `/share/discogs_connector/discogs.sqlite3` or `/data/discogs.sqlite3` into the shared database. Existing unrelated tables in `/share/home_apps.sqlite3` are left intact. If an old generic Discogs schema already exists in the shared file, it is imported and renamed with the `discogs_legacy_v1_` prefix after successful conversion. Do not delete old database files until the new app has started and the collection and release details are visible.

## Collection data and refresh

- The app opens on **Overview**, with profile information and collection statistics such as record count, average recorded release year, year range, decade distribution, and top artists, genres, styles, formats, and labels.
- **Collection** is a searchable table with fixed-size artwork. Select an album title to open its release detail page; the table omits external Discogs links to keep the columns focused.
- A refresh updates the locally stored collection snapshot so additions and removals appear. Automatic refresh is enabled daily by default and can be disabled or set to an interval from 1 hour to 7 days in app Configuration. **Refresh collection** always fetches immediately.
- The first collection load starts a background enrichment pass for missing release tracklists and master details. Requests are paced at about one per second; an initial collection of a few hundred records can take several minutes and may make one release request plus one master request per record. Existing cached rows are skipped, and new collection entries are enriched after later syncs. Invalid master IDs (including `0`) are ignored. Individual 404s are recorded and skipped; transient errors pause the pass and can be retried on the next collection load. App log entries include UTC timestamps and error context. Set `enrich_collection_details` to false to disable this pass.
- Opening a release fetches its full Discogs details, including available tracklist, credits, formats, genres, and styles when they are not already cached. The response is saved in SQLite and reused on later opens.
- The release detail view requests current marketplace listing stats when opened. It labels the lowest current asking price and number of active listings separately from sold prices. Discogs’ public API does not provide the last-30-sales low/median/high summary; use the linked Discogs release page to view its sales history. Marketplace data is not stored by this app.
- Artwork is referenced by Discogs-hosted URLs and is not copied into local storage.
- Credentials remain in Home Assistant app options and are not stored in the database or source repository.
- This app does not request or store collection valuation or sold-sales history and does not write to Discogs.
- Listening history and play counts are not implemented yet; future custom apps can add their own tables in the shared DB with separate prefixes and migrations.

## Install

1. Add this repository in **Settings → Apps → App store → Repositories**.
2. Install or update **Discogs Connector** to version 0.8.0 or newer.
3. In Configuration, confirm the username and enter your Discogs personal access token if needed; save and restart.
4. Open the **Discogs Collection** panel and load or refresh your collection.
5. In SQLite Web, set **Database** to `/share/home_apps.sqlite3`, save, and restart SQLite Web. You should then see the `discogs_` tables alongside other custom-app tables.

## Random record picker

The app selects uniformly from distinct releases in the locally cached `discogs_collection_entries` table. A pick does not call Discogs and does not change the collection tables. It publishes the result and a unique `pick_id` to `sensor.discogs_random_pick`, including release/master year and artwork URLs, format, Discogs URL, pick timestamp, and collection size. On app start, a Supervisor-authenticated WebSocket subscribes only to the `discogs_random_pick_requested` event. The HACS card and the optional Hue automation raise that event with a source label.

While a pick is resolving, the app sends the cached cover URLs from SQLite. The card pre-shuffles a unique sequence sized to fit the approximately six-second animation, excludes the selected cover from the shuffle frames, then holds the already-selected album as the final cover before revealing release details. The cover reel uses a perspective stack with adjacent covers peeking into the frame and an easing upward movement; the card and cover frame retain the same dimensions throughout. The app favors brighter, saturated colors when sampling the selected cover and applies that color directly to the full card background. The animation is only a presentation effect; the chosen release is selected from SQLite and does not depend on the animated sequence.

Install the repository in HACS as a **Dashboard** custom repository, install **Discogs Random Record**, then search for that name in the dashboard card picker. The card element type is `custom:discogs-random-record-card`. HACS installs the resource and handles upgrades. If it does not appear immediately, reload the browser after HACS finishes downloading it.

For Hue Tap Dial Button 4, add this automation to `automations.yaml`:

This simple event automation triggers the dashboard picker. If Button 4 should run the Tidbyt sequence below, use the Tidbyt automation instead of enabling both on the same button.

```yaml
- id: discogs_random_record_from_hue_dial
  alias: Hue dial - Pick a random Discogs record
  mode: single
  triggers:
    - trigger: event.received
      target:
        entity_id: event.unassigned_hue_tap_dial_switch_1_button_4
      options:
        event_type:
          - short_release
  actions:
    - event: discogs_random_pick_requested
      event_data:
        source: hue_button_4
```

The random choice samples unique release IDs, so multiple owned copies of a release do not make it more likely. Separate picks may select the same release; recent-play avoidance can be added later using persistent play history.

### Tidbyt random record picker

The repository includes a Tidbyt custom app and Home Assistant script/automation examples in [`../examples/tidbyt/`](../examples/tidbyt/). Button 4 on the Hue Tap Dial requests a pick from the cached SQLite collection, shows a unique cover shuffle for about six seconds, then shows the selected album for ten seconds. The selected cover is the last shuffle frame, and the details view uses the same 32-pixel cover size. No Discogs request is made during a pick.

1. Copy [`discogspick.star`](../examples/tidbyt/discogspick.star) into TidbytAssistant's configured custom-content directory and install/refresh the `discogspick` app for the `living_room` device.
2. Merge [`random-pick-script.yaml`](../examples/tidbyt/random-pick-script.yaml) into `/config/scripts.yaml`; reload scripts.
3. Add both automations from [`random-pick-automation.yaml`](../examples/tidbyt/random-pick-automation.yaml) to `/config/automations.yaml`; reload automations. If the older simple Button 4 random-pick automation is already enabled, remove it so the button does not request two picks.

The script temporarily pauses the turntable now-playing loop. Turning the dial during the shuffle or details view cancels the random display, deletes its Tidbyt content, and leaves the existing volume automation free to show the volume. After the ten-second display, the script deletes `discogspick` and restarts the now-playing loop if a recognized record is still playing. If the random picker is cancelled by turning the dial, the existing volume override script already resumes now-playing when appropriate.

The app publishes a separate `tidbyt_shuffle_artworks` attribute containing up to seven cached distractor covers followed by the selected cover. The existing `shuffle_artworks` attribute remains the full collection list for the dashboard animation. `discogspick.star` fetches the small cover set and uses the selected album's dominant color for the details background.

Check the TidbytAssistant custom-content path and confirm the installed content ID is `discogspick` before testing. The automation targets `event.unassigned_hue_tap_dial_switch_1_button_4` and rotary events on `event.unassigned_hue_tap_dial_switch_1_rotary`; adjust those IDs if Home Assistant assigned different entity IDs.

The app requires outbound HTTPS access to `api.discogs.com` and Discogs-hosted image URLs.

## Track-facts MCP and Windows PowerShell

Version 0.8.0 adds `search_collection_releases` to the MCP server. It searches the full locally cached collection by artist and album, returning matching owned release IDs, edition metadata, and track/fact coverage. Use this tool first when a user names an album and artist without a release ID; it avoids relying on the first 100 rows of the needs-facts list. The search is local and read-only. The server also lists releases needing facts, fetches one release's cached tracks and metadata, replaces five facts for every song track on one release, and verifies saved rows. The write tool validates every exact `track_key`, requires exactly five linked facts per song, each written as a concise two-to-three-sentence detail with its source, and commits all rows for the selected release in one transaction with `status='complete'`.

The endpoint is separate from the ingress panel and requires a random bearer token of at least 32 characters. Do not expose port 8100 to the public internet.

1. Update **Discogs Connector** to 0.8.1 or newer. In its **Configuration**, set `facts_mcp_token` to a random token of at least 32 characters. Save and restart the app. Confirm the app log says the track-facts MCP is listening on port 8100.
2. In Windows PowerShell, create and save a random token to your user environment, and copy it to the clipboard:

   ```powershell
   $bytes = New-Object byte[] 32
   $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
   $rng.GetBytes($bytes)
   $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
   $rng.Dispose()
   [Environment]::SetEnvironmentVariable('DISCOGS_CONNECTOR_MCP_TOKEN', $token, 'User')
   $env:DISCOGS_CONNECTOR_MCP_TOKEN = $token
   Set-Clipboard -Value $token
   ```

   Paste the clipboard value into `facts_mcp_token` in Home Assistant app Configuration, then save and restart the app. The environment variable keeps the token out of Codex's MCP config file.
3. From the same PowerShell session, register the MCP server with Codex:

   ```powershell
   codex mcp add discogs-connector --url http://homeassistant.local:8100/mcp --bearer-token-env-var DISCOGS_CONNECTOR_MCP_TOKEN
   codex mcp list
   ```

   If the server is already registered, remove it first with `codex mcp remove discogs-connector`. Fully restart Codex after registration so its process receives the saved Windows environment variable. The CLI and desktop Codex share the MCP configuration.
4. If port 8100 is occupied, change the host-side mapping for container port 8100 in **Settings → Apps → Discogs Connector → Configuration → Network** and use that host port in the Codex URL.

Once connected, the Discogs track-facts skill can select an album, load its exact track keys, research facts, write them for all tracks, and verify the result without SQL export/import or an approval pause. SQLite Web remains useful for manual inspection; it is not required for Codex. Avoid opening the live SQLite file directly from Windows over Samba. Keep database operations inside this app or use its network API.

### PowerShell and Home Assistant CLI

Windows PowerShell can call the MCP endpoint over HTTP. Windows OpenSSH is another way to connect to Home Assistant if you install and configure its **Terminal & SSH** app. Home Assistant's `ha` CLI is for Home Assistant and app administration, logs, and backups; it is not a general SQL client. The direct MCP connection is the shortest path for Codex.

## Attribution

This application uses Discogs' API but is not affiliated with, sponsored, or endorsed by Discogs. Discogs is a trademark of Zink Media, LLC. Collection results and detail pages attribute and link to Discogs.
