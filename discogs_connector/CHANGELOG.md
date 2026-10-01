# 0.8.0

- Add an MCP tool to search the full locally cached Discogs collection by artist and album, returning owned release IDs, edition details, and track-fact coverage.
- Allow named album requests to resolve their exact cached collection release without depending on the first 100 needs-facts candidates.

# 0.7.9

- Add a bearer-token-protected, stateless MCP endpoint with scoped tools for choosing collection releases, reading track context, saving five facts per song in one transaction, and verifying saved rows.
- Keep MCP on a dedicated mapped port; it does not expose arbitrary SQL or the existing ingress web panel.

# 0.7.8

- Add a bounded, unique Tidbyt shuffle-cover sample while preserving the full artwork list used by the dashboard card.
- Provide a two-stage random-record Tidbyt display and Home Assistant Hue Dial examples with volume interruption handling.

# 0.7.7

- Isolate track-fact table creation in its own schema initializer for a simpler startup migration path.

# 0.7.6

- Add durable per-track fact tables for researched track facts and their source references; collection refreshes leave these records intact.
- Reserve five ordered fact slots per track and track fact-set provenance, status, and generation metadata.

# 0.7.5

- Select a brighter, more saturated artwork color while down-weighting dark shadows, and log the sampled color for each pick.
- Apply the color directly to the full card background and update rendering when the sensor color changes.
- Replace the cover flip with a perspective reel: adjacent covers peek into the frame and each unique cover eases upward into place as the shuffle slows.

# 0.7.4

- Sample a dark dominant color from the selected Discogs cover in the app and publish it with the pick, avoiding browser cross-origin restrictions.
- Send the complete cached artwork URL set to the card; pre-shuffle a unique sequence for the visible frames, omit the selected cover from that sequence, and hold the selected cover as the final frame.
- Keep the random animation duration unchanged.

# 0.7.3

- Expand the shuffle artwork sample to 50 cached covers.
- Hold the selected cover before revealing details, keep the card geometry stable, and tint the result with a dominant color sampled from its cover when browser access permits.
- Slow the opening shuffle flips while retaining the existing total animation duration.

# 0.7.2

- Animate random picks by shuffling through a sample of locally cached collection artwork, then reveal the chosen release details.

# 0.7.1

- Enable the Home Assistant Core API proxy required for the random-pick event listener and sensor updates.

# 0.7.0

- Add a cached-collection random picker and publish selected release/master metadata through `sensor.discogs_random_pick`.
- Add the HACS Dashboard card bundle for animated random selection and album details.
- Listen for the `discogs_random_pick_requested` Home Assistant event for dashboard and Hue Dial triggers.

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

