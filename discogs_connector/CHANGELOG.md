# 0.6.1

- Ignore invalid zero master IDs and continue enrichment after individual Discogs 404 responses.
- Remember not-found metadata IDs to avoid repeatedly requesting permanent 404s; transient errors pause the pass and remain retryable.
- Add UTC timestamps and HTTP status, URL, and response details to app log errors.

# 0.6.0

- Replace the collection cache TTL with an optional configurable scheduled refresh (daily by default); manual refresh remains available.
- Reuse cached collection and release details until an explicit or scheduled collection refresh.

# 0.5.0

- Add the normalized `discogs_masters` table for Discogs master year and primary artwork URLs.
- Incrementally enrich collection releases with normalized tracklists and master data in a background worker, paced to stay under normal Discogs request rates.
- Keep collection rows and stable Discogs IDs intact during the schema migration.

