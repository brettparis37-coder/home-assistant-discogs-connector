# Home Assistant Discogs Connector

This Home Assistant app provides an ingress collection browser for the Discogs account configured in its options (default username: `IPAIRIS`).

## Shared database and schema

The app stores its data in `/share/home_apps.sqlite3`, a shared custom-app database. It is separate from Home Assistant's recorder database. The tables owned by this app use the `discogs_` prefix; migration bookkeeping is in `app_schema_versions`, keyed by app ID. This lets other custom apps add their own namespaced tables to the same SQLite file without sharing ownership of schema changes. Each app must manage its own schema version and migrations.

The schema is relational for searching and joins:

- `discogs_releases` stores one row per Discogs release.
- `discogs_collection_entries` stores your collection instances and folder/date-added fields.
- `discogs_artists`, `discogs_release_artists`, `discogs_labels`, and `discogs_release_labels` store reusable artist/label records and release relationships.
- `discogs_release_formats`, `discogs_format_descriptions`, and `discogs_release_classifications` store formats, genres, and styles.
- `discogs_tracks`, `discogs_track_credits`, and `discogs_release_credits` store tracklists and credits when a release detail page is opened.
- `discogs_release_payloads` retains the API response for cache reuse and fields not yet represented as columns. Normalized tables are the queryable representation for common lookups.
- `discogs_collection_sync` records the latest collection refresh time and item count.

Schema migrations do not use SQLite's file-global `PRAGMA user_version`, so one app will not overwrite another app's version marker. SQLite Web can view the shared file by setting its **Database** option to `/share/home_apps.sqlite3`; its Home Assistant app configuration provides one database path at a time.

## Upgrade and data migration

On first startup of version 0.3.0, the app preserves and imports existing Discogs data from `/share/discogs_connector/discogs.sqlite3` or `/data/discogs.sqlite3` into the shared database. Existing unrelated tables in `/share/home_apps.sqlite3` are left intact. If an old generic Discogs schema already exists in the shared file, it is imported and renamed with the `discogs_legacy_v1_` prefix after successful conversion. Do not delete old database files until the new app has started and the collection and release details are visible.

## Collection data and refresh

- A refresh replaces the locally stored collection snapshot so new additions and removals appear. The default cache interval is four hours; **Refresh collection** fetches immediately.
- Opening a release fetches its full Discogs details, including available tracklist, credits, formats, genres, and styles. Those details are cached in SQLite and reused for the configured cache interval.
- Artwork is referenced by Discogs-hosted URLs and is not copied into local storage.
- Credentials remain in Home Assistant app options and are not stored in the database or source repository.
- This app does not request collection value or sales-history data and does not write to Discogs.
- Listening history and play counts are not implemented yet; future custom apps can add their own tables in the shared DB with separate prefixes and migrations.

## Install

1. Add this repository in **Settings → Apps → App store → Repositories**.
2. Install or update **Discogs Connector** to version 0.3.0.
3. In Configuration, confirm the username and enter your Discogs personal access token if needed; save and restart.
4. Open the **Discogs Collection** panel and load or refresh your collection.
5. In SQLite Web, set **Database** to `/share/home_apps.sqlite3`, save, and restart SQLite Web. You should then see the `discogs_` tables alongside other custom-app tables.

The app requires outbound HTTPS access to `api.discogs.com` and Discogs-hosted image URLs.

## Attribution

This application uses Discogs' API but is not affiliated with, sponsored, or endorsed by Discogs. Discogs is a trademark of Zink Media, LLC. Collection results and detail pages attribute and link to Discogs.

