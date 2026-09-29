# Home Assistant Discogs Connector

Standalone Home Assistant app for browsing the Discogs collection owned by `IPAIRIS` (or another configured username).

The app is in [`discogs_connector/`](discogs_connector/). It provides a profile and collection overview, a searchable table with fixed-size cover art, release detail pages with tracklists, and a random record picker. The picker uses only the locally cached collection and publishes its choice as `sensor.discogs_random_pick`. It uses a normalized local SQLite schema. Collection and opened release data are stored in the shared custom-app database at `/share/home_apps.sqlite3`; setup, schema, migration, and refresh behavior are documented in [`discogs_connector/DOCS.md`](discogs_connector/DOCS.md).

This repository also contains a HACS Dashboard card in [`dist/home-assistant-discogs-connector.js`](dist/home-assistant-discogs-connector.js). Install this same GitHub repository in both Home Assistant Apps and HACS (as a **Dashboard** custom repository) to get the app and card updates from one source. The app uses Home Assistant's internal Core API proxy to receive picker events and publish its selection sensor.

Tables owned by this app use the `discogs_` prefix. The shared file is intended for separate custom apps to add their own namespaced tables and independently versioned migrations. It is separate from Home Assistant's recorder database. SQLite Web can inspect it by setting its Database option to `/share/home_apps.sqlite3`.

Enter the Discogs personal access token in Home Assistant app Configuration after installation. Never commit tokens to source control.

## HACS dashboard card

In HACS, open **Frontend → ⋮ → Custom repositories**, add this repository URL, and choose **Dashboard** as the category. Download **Discogs Random Record**, then add the card in dashboard edit mode by searching for **Discogs Random Record** or using `type: custom:discogs-random-record-card`. HACS serves the bundled resource and handles later card updates. The card uses `sensor.discogs_random_pick`, which the app creates the first time a pick is requested.

The card raises `discogs_random_pick_requested` inside Home Assistant. The app listens through the Supervisor’s internal WebSocket, selects one distinct release from its cached collection, and updates the sensor. A pick does not call Discogs. Load or refresh the collection once in the app before trying the picker.

To use Hue Tap Dial Button 4, add this automation to `automations.yaml` (or recreate it in the automation editor):

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

The card reels through a randomized, non-repeating sequence of cached collection covers, easing each cover upward as the shuffle slows, then holds the preselected album as the final frame before revealing its details. The card and cover frame keep the same dimensions through the transition. The app samples a vivid color from the selected cover and applies it directly to the full card background, so the result does not depend on browser cross-origin image access. The cover list comes from the local database; selecting a record does not make extra Discogs API calls. The release image is preferred; master artwork is used if the collection entry has no release image. Selection allows repeats across separate spins.

An optional Tidbyt renderer and Home Assistant automation example are in [`examples/tidbyt/`](examples/tidbyt/). They show the picked record on the living-room Tidbyt for 15 seconds and then restore the turntable now-playing display when a recognized record is still playing. These files are examples and are not installed automatically by HACS or the app.

