# Home Assistant Discogs Connector

A standalone Home Assistant app that provides an ingress collection browser for the Discogs account configured in the app options (default username: `IPAIRIS`).

## Data and storage

- Collection metadata is stored in SQLite at `/data/discogs.sqlite3`, inside the app's persistent data volume.
- Collection refreshes replace the local collection snapshot so additions and removals are reflected. The default refresh/cache interval is four hours; use **Refresh collection** to fetch immediately.
- Opening a release fetches its full Discogs release details, including the available tracklist, and saves that response in SQLite. The app reuses release details for the configured cache interval, then refreshes them when opened again.
- Album artwork is referenced by Discogs-hosted image URLs and is not copied to local storage.
- Credentials remain in Home Assistant app options and are not stored in the database or source repository.
- This version does not yet create listening-history or play-count records. The database is structured so a separate play-history table can be introduced with the future recognition integration.

The collection and opened release metadata are retained until a later refresh replaces or updates them, or the local database is removed. The cache interval controls when the app requests newer Discogs data; it does not automatically delete all retained records at expiry.

## Features

- Browse and search a collection by artist, release title, year, format, label, or catalog number.
- Open a local release detail page with track positions, track lengths, labels, formats, genres, styles, credits, and available notes.
- Refresh the paginated collection from Discogs on demand or when the stored snapshot expires.
- Link to each Discogs release and attribute Discogs-provided information.

## Install

1. Add this repository in **Settings → Apps → App store → Repositories**.
2. Install **Discogs Connector**.
3. Open app Configuration; confirm the username and enter a Discogs personal access token.
4. Save and start the app, then open its **Discogs Collection** panel.

The app requires outbound HTTPS access to `api.discogs.com` and Discogs-hosted image URLs.

## Limitations

- No pricing or sales-history data is requested or stored.
- No collection writes are made to Discogs.
- Full release details are fetched only when a release is opened, rather than calling the release endpoint for every item during collection refresh.
- Play-history capture and track matching are future work.
- Recognition artwork in Turntable Recognition remains sourced independently from AudD.

## Attribution

This application uses Discogs' API but is not affiliated with, sponsored, or endorsed by Discogs. Discogs is a trademark of Zink Media, LLC. Collection results and detail pages attribute and link to Discogs.

