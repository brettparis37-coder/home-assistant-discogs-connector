# Home Assistant Discogs Connector

Standalone Home Assistant app repository for browsing the Discogs collection owned by `IPAIRIS`.

The add-on source is in [`discogs_connector/`](discogs_connector/). It provides a searchable collection panel inside Home Assistant, fetches data on demand, uses an in-memory cache only, and does not save collection records, artwork, or pricing to disk. Setup steps and scope are in [`discogs_connector/DOCS.md`](discogs_connector/DOCS.md).

The Discogs token is entered in the Home Assistant app Configuration after installation. Never commit it to this repository.
