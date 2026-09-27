# Home Assistant Discogs Connector

Standalone Home Assistant app for browsing the Discogs collection owned by `IPAIRIS` (or another configured username).

The app is in [`discogs_connector/`](discogs_connector/). It provides a profile and collection overview, a searchable table with fixed-size cover art, and release detail pages with tracklists, credits, and current marketplace listing information. It uses a normalized local SQLite schema. Collection and opened release data are stored in the shared custom-app database at `/share/home_apps.sqlite3`; setup, schema, migration, and refresh behavior are documented in [`discogs_connector/DOCS.md`](discogs_connector/DOCS.md).

Tables owned by this app use the `discogs_` prefix. The shared file is intended for separate custom apps to add their own namespaced tables and independently versioned migrations. It is separate from Home Assistant's recorder database. SQLite Web can inspect it by setting its Database option to `/share/home_apps.sqlite3`.

Enter the Discogs personal access token in Home Assistant app Configuration after installation. Never commit tokens to source control.
