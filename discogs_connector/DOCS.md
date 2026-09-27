# Home Assistant Discogs Connector

A standalone Home Assistant app that provides an ingress collection browser for the Discogs account `IPAIRIS`.
Collection data is fetched from Discogs on demand and kept in process memory only. The app does not write collection
records, artwork, or price history to `/data` or the repository. Its memory cache defaults to four hours (configurable
from 15 to 300 minutes) and is cleared when the app restarts. The app refuses to present cached results after six hours.

## First version

- Browse and search a collection by artist, release title, year, format, label, or catalog number.
- Show Discogs-hosted thumbnail images with links to the source release and required attribution.
- Refresh the complete collection on demand; the Discogs API response is paginated.
- Keep authentication token in Home Assistant app options; never commit it.
- No price lookups, price history, collection writes, or persistent database in this version.

## Install for local testing

1. Copy this folder into a Home Assistant add-on repository, or point a local app repository at it.
2. Add that repository in **Settings → Apps → App store → Repositories**.
3. Install **Discogs Connector**.
4. Open app Configuration; confirm username `IPAIRIS` and enter the Discogs personal access token.
5. Save and start the app, then open its **Discogs Collection** sidebar panel.

The add-on requires outbound HTTPS access to `api.discogs.com` and Discogs-hosted image URLs.

## Limitations

The ingress view is the collection-search surface. This version does not create hundreds of recorder-backed entities in
Home Assistant. It makes a live collection browser available inside Home Assistant without turning API results into a
permanent local mirror. Recognition artwork in Turntable Recognition remains sourced independently from AudD.

## Attribution

This application uses Discogs' API but is not affiliated with, sponsored, or endorsed by Discogs. Discogs is a trademark
of Zink Media, LLC. Collection data is attributed and linked to its Discogs release page in the app.
