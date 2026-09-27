# Home Assistant Discogs Connector

Standalone Home Assistant app repository for browsing the Discogs collection owned by `IPAIRIS`.

The app source is in [`discogs_connector/`](discogs_connector/). It provides a searchable collection panel inside Home Assistant, stores collection metadata and opened release details in SQLite under `/share/discogs_connector/` so SQLite Web can inspect it, and loads Discogs-hosted artwork by URL. Setup and data-retention notes are in [`discogs_connector/DOCS.md`](discogs_connector/DOCS.md).

The Discogs token is entered in the Home Assistant app Configuration after installation. Never commit it to this repository.

