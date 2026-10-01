---
name: discogs-track-facts
description: Find collection albums without saved track facts, gather Discogs track context, research engaging song stories, and store five facts per track in the Home Assistant SQLite database when a database tool is connected.
---

# Discogs Track Facts

Use this skill for the user's vinyl collection and shared Home Assistant SQLite database. The Discogs Connector stores normalized collection and track data plus `discogs_track_fact_sets` and `discogs_track_facts`.

## Database access and execution

At the start of a run, check whether the `discogs-connector` MCP server is connected. Prefer its `search_collection_releases`, `list_releases_needing_facts`, `get_release_tracks`, `save_release_facts`, and `verify_release_facts` tools to query, research, insert, and verify in one run. If the user names an artist and album but `search_collection_releases` is not available, the connected add-on is outdated: update/restart it and reconnect MCP before asking for a release ID. Do not stop for fact approval or ask the user to review generated facts.

Do not treat Home Assistant's built-in MCP server as a SQL interface: it exposes Assist API tools, not arbitrary database queries. The community HTTP MCP server has a general `call_service` tool, but no direct SQLite table tools; the Home Assistant SQL integration's `sql.query` action is read-only. SQLite Web is a browser-based database viewer/editor and can help inspect tables or run SQL manually, but it is not a documented MCP/API for direct Codex calls. Installing any of these alone does not establish both live reads and fact writes for this skill.

If no scoped live database tool is connected, do not claim to have queried or written the live database. Continue using the SQL workflow below, save the runnable transaction as a `.sql` file in the project's `outputs` folder when available, and explain that direct execution awaits database-tool setup. Never ask the user to paste an access token into chat.

## Workflow

1. Album selection: if the user supplies a Discogs release ID, use it directly. If the user names an artist and album, use `search_collection_releases({artist, album})` to search the full locally cached collection and resolve the owned release ID; do not scan `list_releases_needing_facts` or ask the user for an ID. Prefer exact artist/title matches. If multiple editions match, use the exact title/artist edition with a cached tracklist and missing facts; if several are equally suitable, prefer the most recently added edition and retain its exact `release_id`. If the user requests a special/deluxe edition, honor that title. If no match is returned, state that the current connector cannot find it in its local collection cache and advise refreshing/loading the Discogs Connector collection or installing the version that provides `search_collection_releases`; do not guess a public Discogs ID or silently use a different edition. If the user gives only an album title and the connector returns multiple artists, use the best clear exact match; ask only if artist identity remains genuinely ambiguous. If no album is named, list releases needing facts and wait for the user to choose. Once the album is resolved, continue through research, write, and verification without another approval or fact-review pause. Without the connector, provide the collection query below. Use each actual `track_key` exactly; it may be based on a Discogs track ID or a sequence fallback and is not necessarily `release_id:sequence`.
2. Fetch all track and album context for the selected release through the live tool when connected, or provide the album detail query below as the fallback. Do not invent track rows or keys. Use every returned song track and preserve each returned `track_key` literally, including sequence-based fallback keys. Some Discogs releases expose only a release-sequence key rather than a Discogs track ID; never try to reconstruct or normalize it. Trust the connector's returned song-track list and do not add headings, indexes, or non-song rows unless the connector marks them as song tracks.
3. Research five interesting facts for each requested track, each about 2–3 readable sentences. Prioritize facts in this order: (a) track-specific story, musical, lyrical, recording, personnel, or scene detail; (b) additional unique album/release facts; (c) unique facts about the film/work and its production, story, reception, setting, or collaborators that meaningfully connect to this track; (d) track-relevant composer/artist background. For soundtracks, scene placement, story/character function, diegetic music, and composer/director collaboration are valid angles. Exhaust distinct album and film/work facts before falling back to repeated general album or score context. Keep a per-release fact ledger while drafting: record the claim/topic already used and do not repeat it across tracks unless no distinct, well-supported fact remains. Do not use templated openers or sentence frames (for example, “sits within the film’s musical world,” “this track contributes to the score,” or equivalent filler). Every sentence should add a new supported detail; merely swapping track titles into the same broad statement does not make facts unique. Fresh wording is required, but wording variation alone does not make a repeated claim unique. If repetition is unavoidable, state the distinct track-specific connection and reuse the broader claim only as a last resort. Do not invent scene placement or production history, or present interpretation as artist intent. Clearly attribute interpretations and anecdotes; identify listening observations as audible qualities rather than claims about how the recording was made. Source each factual claim directly. Never leave a track unsaved or stop to ask the user for fact approval solely because the reliable material is broader than the track; use the strongest relevant broader context and continue. Research with web search/open when claims are niche or precise sourcing is needed.
4. Write in a warm, story-forward voice. Do not mention the act of researching, source gaps, future improvements, or instructions to replace/check a fact later. Avoid self-referential filler such as “this is a useful fact to replace if a better source turns up.” Do not invent details. If a claim is disputed or anecdotal, attribute it plainly (“In a later interview…”, “One account says…”) instead of presenting it as settled fact.
5. Source quality and claim alignment are required. Prefer artist/label archives, direct interviews, liner notes, the film/game/work itself or official materials, contemporary reporting, reputable music publications, and academic sources. Do not use Wikipedia, fan wikis, Reddit, generic AI-generated song-meaning pages, search snippets, or broad album reviews as the sole support for a specific factual claim. Open each page and confirm it actually supports the fact; search-result snippets are leads only. The source must support every specific factual sentence in its fact_text, not merely mention the album or song. For scene descriptions, cite a directly relevant interview, official screenplay/production notes, or the work itself where available; distinguish on-screen events from interpretation. Attribute interpretations to the critic/source (“The Ringer reads…”) and report anecdotes as attributed accounts. Cross-check high-impact or disputed claims where practical. Do not cite the same generic album overview repeatedly to manufacture five facts. The database has only one source URL field per fact, so store the strongest directly relevant source in that row; include a concise list of the principal sources in the final response.
6. Once facts are researched, immediately write them through the scoped database tool when available, then verify status, fact count, and fact rows. Do not pause for approval or ask the user to review facts. Replace prior facts only for the exact included `track_key` values; leave all other tracks and releases alone. Mark fully researched and sourced facts `complete` so they do not enter a review queue. The connector write is release-wide and atomic: it rejects empty or partial submissions and expects the entire set of song tracks, with exactly five facts each, in one call. Before writing, validate that every returned song track appears exactly once, has five distinct non-empty 2–3 sentence facts, and each fact has a direct source title, valid HTTP(S) URL, and publisher. Manually check fact-to-source alignment and distinctness; do not assume payload size or successful insertion means the research is sound. Check the total (`song track count × 5`) before the call. **Payload shape:** `tracks` must be an array of objects shaped as `{ track_key: string, facts: [{ fact_text, source_title, source_url, source_publisher }, ...] }`; `facts` is an array of five fact objects, not an array of five strings and not a nested array. Validate the structure (for example, by checking each track has `facts.length === 5` and each fact has all four string fields) before calling the connector. **Source-key validation:** if building facts from a keyed source registry, validate every fact's source key exists before mapping it to source metadata; then validate that the resolved title, URL, and publisher are non-empty strings and that the URL is HTTP(S). Never call the connector with unresolved aliases or undefined source fields. Build a small preflight summary showing track count, facts per track, resolved source count, and expected total; abort and repair if any check fails. Keep the full release in one call because each call replaces facts across the selected release. If validation fails, repair the payload before calling. After save, call verification and confirm every track is `complete`, `fact_count` is five, and `stored_fact_count` is five. When using the SQL fallback, generate one transaction using the exact album and track rows already supplied and set `fact_count` to the actual inserted count, normally five.
7. Save a `.sql` file with the transaction and verification query when using the fallback. Escape every apostrophe in SQL text as `''`. For a successful live write, report the release and track counts, verification result, and a concise list of sources used; do not also require the user to run SQL manually. Never expose raw fact payloads as a review step unless the user asks to see them.

## Find collection albums that still need facts

```sql
WITH collection_releases AS (
  SELECT DISTINCT release_id
  FROM discogs_collection_entries
), track_coverage AS (
  SELECT
    t.release_id,
    COUNT(*) AS track_count,
    SUM(CASE
      WHEN fs.status IN ('complete', 'needs_review')
       AND fs.fact_count = 5
       AND (SELECT COUNT(*) FROM discogs_track_facts AS f WHERE f.track_key = t.track_key) = 5
      THEN 1 ELSE 0 END) AS facted_track_count
  FROM discogs_tracks AS t
  LEFT JOIN discogs_track_fact_sets AS fs ON fs.track_key = t.track_key
  WHERE t.track_type = 'track'
  GROUP BY t.release_id
)
SELECT
  r.release_id,
  r.title AS album,
  (SELECT group_concat(a.name, ', ')
   FROM (
     SELECT DISTINCT da.name
     FROM discogs_release_artists AS ra
     JOIN discogs_artists AS da USING (artist_key)
     WHERE ra.release_id = r.release_id
     ORDER BY da.name
   ) AS a) AS artists,
  r.year AS release_year,
  m.year AS master_year,
  COALESCE(tc.track_count, 0) AS track_count,
  COALESCE(tc.facted_track_count, 0) AS facted_track_count,
  COALESCE(tc.track_count, 0) - COALESCE(tc.facted_track_count, 0) AS tracks_missing_facts
FROM collection_releases AS cr
JOIN discogs_releases AS r USING (release_id)
LEFT JOIN discogs_masters AS m USING (master_id)
LEFT JOIN track_coverage AS tc USING (release_id)
WHERE COALESCE(tc.facted_track_count, 0) < COALESCE(tc.track_count, 0)
ORDER BY tracks_missing_facts DESC, artists, album;
```

## Get all track and release context for a chosen album

Replace `123456` with the selected `release_id`.

```sql
SELECT
  t.track_key,
  r.release_id,
  t.sequence AS track_sequence,
  t.position,
  t.title AS track_title,
  t.duration,
  t.duration_ms,
  r.title AS album,
  r.year AS release_year,
  r.released AS exact_release_date,
  r.country,
  r.uri AS discogs_release_uri,
  m.year AS master_year,
  (SELECT group_concat(a.name, ', ')
   FROM (
     SELECT DISTINCT da.name
     FROM discogs_release_artists AS ra
     JOIN discogs_artists AS da USING (artist_key)
     WHERE ra.release_id = r.release_id
     ORDER BY da.name
   ) AS a) AS album_artists,
  (SELECT group_concat(a.name, ', ')
   FROM (
     SELECT DISTINCT da.name
     FROM discogs_track_credits AS tc
     JOIN discogs_artists AS da USING (artist_key)
     WHERE tc.track_key = t.track_key
     ORDER BY da.name
   ) AS a) AS track_artists,
  (SELECT group_concat(value, ', ')
   FROM discogs_release_classifications AS c
   WHERE c.release_id = r.release_id AND c.kind = 'genre') AS genres,
  (SELECT group_concat(value, ', ')
   FROM discogs_release_classifications AS c
   WHERE c.release_id = r.release_id AND c.kind = 'style') AS styles,
  (SELECT group_concat(label_name, '; ')
   FROM (
     SELECT dl.name || CASE WHEN rl.catalog_number != '' THEN ' [' || rl.catalog_number || ']' ELSE '' END AS label_name
     FROM discogs_release_labels AS rl
     JOIN discogs_labels AS dl USING (label_key)
     WHERE rl.release_id = r.release_id
     ORDER BY rl.position
   )) AS labels_and_catalog_numbers,
  fs.status AS facts_status,
  fs.fact_count AS saved_fact_count
FROM discogs_tracks AS t
JOIN discogs_releases AS r USING (release_id)
LEFT JOIN discogs_masters AS m USING (master_id)
LEFT JOIN discogs_track_fact_sets AS fs ON fs.track_key = t.track_key
WHERE r.release_id = 123456
  AND t.track_type = 'track'
ORDER BY t.sequence;
```

The query output supplies identities for safe inserts and context for research. Do not substitute a release/master ID for the track's exact `track_key`.

### Fact table schema

Use these exact columns when generating SQL for the user's database:

- `discogs_track_fact_sets`: `track_key`, `release_id`, `track_sequence`, `track_title_snapshot`, `artist_snapshot`, `album_snapshot`, `status`, `provider`, `model`, `prompt_version`, `generated_at`, `fact_count`, `error` (timestamps `created_at` and `updated_at` are database-managed).
- `discogs_track_facts`: `track_key`, `fact_order`, `fact_text`, `source_title`, `source_url`, `source_publisher` (`fact_id` and `created_at` are database-managed).

## Insert or replace researched facts

Generate a transaction from the selected album's actual query results. For each included track:

- Upsert `discogs_track_fact_sets` using the exact `track_key`, `release_id`, `track_sequence`, `track_title_snapshot`, `artist_snapshot`, and `album_snapshot`. Set `status='complete'`, `provider='Codex research'`, `model=''` unless a model value is known and requested, `prompt_version='discogs-track-facts-v2'`, `generated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')`, `fact_count`, and `error=''`.
- Do not name database-managed columns such as `created_at`, `updated_at`, or `fact_id` in inserts.
- Delete `discogs_track_facts` only for that exact `track_key`, then insert its ordered facts with source title, URL, and publisher.
- Wrap the album's updates in `BEGIN; ... COMMIT;` so the fact sets and their rows stay consistent.
- When working in a local project, save the finished runnable transaction as a `.sql` file in the project's user-facing outputs folder when one exists, and give the user a direct file link. Include the verification query in the file.

Use `INSERT ... ON CONFLICT(track_key) DO UPDATE` for the set and `INSERT` for fact rows after the targeted delete. Do not alter unrelated records. Keep each fact's source URL as a single valid URL; never put multiple URLs into the URL field.

Verification query template:

```sql
SELECT s.release_id, s.track_sequence, s.status, s.fact_count,
       f.fact_order, f.fact_text, f.source_title, f.source_url, f.source_publisher
FROM discogs_track_fact_sets AS s
JOIN discogs_track_facts AS f USING (track_key)
WHERE s.release_id = 123456
ORDER BY s.track_sequence, f.fact_order;
```
