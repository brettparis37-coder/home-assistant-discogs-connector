# Discogs Track Facts for Codex

This plugin packages the Discogs Track Facts skill and the Home Assistant Discogs Connector MCP server configuration. It can search an artist and album in the locally cached Discogs collection, generate five distinct sourced facts per song track, save them, and verify the write.

## Requirements

- Home Assistant running the Discogs Connector add-on with the track-facts MCP endpoint enabled on port 8100.
- Codex must be able to resolve and reach `http://homeassistant.local:8100/mcp`.
- Set `DISCOGS_CONNECTOR_MCP_TOKEN` in the environment used to launch Codex. Keep the token out of this repository.

## Install from this repository

In Codex Desktop, open **Plugins → Install from Marketplace** and choose this repository's marketplace at `.agents/plugins/marketplace.json`, then install **Discogs Track Facts**. If the repository was cloned locally, open it as a trusted Codex project and restart Codex Desktop if the marketplace does not appear.

To add this GitHub repository as a marketplace source, use Codex's plugin marketplace interface and provide:

```
https://github.com/brettparis37-coder/home-assistant-discogs-connector
```

The plugin is listed in that repository's `.agents/plugins/marketplace.json`.

## Configure the token in Windows

The plugin reads `DISCOGS_CONNECTOR_MCP_TOKEN` as a bearer token. Set it in the environment from which Codex Desktop is launched. For a persistent user-level variable in PowerShell:

```powershell
[Environment]::SetEnvironmentVariable('DISCOGS_CONNECTOR_MCP_TOKEN', '<your-token>', 'User')
```

Then fully exit and restart Codex Desktop so the app inherits the updated environment. Do not put the token in `mcp.json`, `plugin.json`, or any committed file.

## Keep the installed plugin current

The repository is the source of truth for the plugin package. After changes are published to GitHub, refresh/upgrade the marketplace in **Plugins** and update the installed plugin. Changes to this package do not automatically update an already installed cached copy.

## Data handling

The skill performs no approval/review pause for generated facts. It researches all tracks in the selected release, writes the complete release, and verifies the stored facts. The plugin writes only through the named, release-scoped MCP tools; it does not expose arbitrary SQL. The Home Assistant endpoint and token are private to the user's local setup and are not suitable for the universal public plugin directory.
