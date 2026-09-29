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
2. Install or update **Discogs Connector** to version 0.7.0.
3. In Configuration, confirm the username and enter your Discogs personal access token if needed; save and restart.
4. Open the **Discogs Collection** panel and load or refresh your collection.
5. In SQLite Web, set **Database** to `/share/home_apps.sqlite3`, save, and restart SQLite Web. You should then see the `discogs_` tables alongside other custom-app tables.

## Random record picker

The app selects uniformly from distinct releases in the locally cached `discogs_collection_entries` table. A pick does not call Discogs and does not change the collection tables. It publishes the result and a unique `pick_id` to `sensor.discogs_random_pick`, including release/master year and artwork URLs, format, Discogs URL, pick timestamp, and collection size. On app start, a Supervisor-authenticated WebSocket subscribes only to the `discogs_random_pick_requested` event. The HACS card and the optional Hue automation raise that event with a source label.

Install the repository in HACS as a **Dashboard** custom repository, install **Discogs Random Record**, then search for that name in the dashboard card picker. The card element type is `custom:discogs-random-record-card`. HACS installs the resource and handles upgrades. If it does not appear immediately, reload the browser after HACS finishes downloading it.

For Hue Tap Dial Button 4, add this automation to `automations.yaml`:

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

### Optional Tidbyt preview

The repository includes [`../examples/tidbyt/discogspick.star`](../examples/tidbyt/discogspick.star) and [`../examples/tidbyt/random-pick-automation.yaml`](../examples/tidbyt/random-pick-automation.yaml). Copy `discogspick.star` into the TidbytAssistant app's configured Tidbyt content directory, then add the automation item to `automations.yaml`. It briefly overrides the living-room display for 15 seconds, then restarts the turntable now-playing script if playback remains recognized. The automation skips its display while the existing volume override script is active. Validate your TidbytAssistant custom-content path before enabling it.

The app requires outbound HTTPS access to `api.discogs.com` and Discogs-hosted image URLs.

## Attribution

This application uses Discogs' API but is not affiliated with, sponsored, or endorsed by Discogs. Discogs is a trademark of Zink Media, LLC. Collection results and detail pages attribute and link to Discogs.
