# Changelog

All notable changes to PlexMix will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.13.0] - 2026-11-18

### Added
- Add `DESIGN.md` (with an `.impeccable/design.json` sidecar) documenting the web UI's design system: tokens, components, and the rules for using them, so new screens stay consistent.
- Add a skip link, a main landmark and a page heading to every Web UI page.
- Sort the Library by Title, Artist or Album and see each track's tag, embedding, audio and MusicBrainz status at a glance.
- Show your recent prompts in the command palette, and offer "Generate “…”" for any text that matches no command.
- Press ⌘/Ctrl+Enter in the Generator prompt to generate.
- Add year range, tag and "has audio analysis" filters to the Library filter bar.
- Add a guided first-run checklist on the Dashboard (Connect Plex → Choose providers → Sync → Build search index → First vibe) that works entirely in the browser.
- Show which embedding provider will run and whether it's ready before generating; show rerun filters as removable chips instead of applying them invisibly.
- Show library coverage on the Dashboard (Tagged, Embedded, Audio analyzed, MusicBrainz genres) as counts and percentages with a link to fill each gap, plus when the library last synced; recent playlists now open in History.
- Add `--allow-removals` to `plexmix sync`, `plexmix sync incremental`, `plexmix sync full` and `plexmix sync regenerate`. Use it for a sync that runs without a terminal (cron, a script, `docker exec` without `-it`) and that may remove a large number of tracks without the confirmation question. A removal is large when it is more than 50 tracks and more than 5 percent of the library.
- Back up the library before a large removal. PlexMix copies the database and the search index files into a new dated folder under `backups/` beside the database (by default `~/.plexmix/backups/<date>_<time>/`) and prints the folder and its size. This also holds for a sync that runs without embeddings (`--no-embeddings`, or the sync that `plexmix doctor` offers): the copy holds the index files as they are on disk, and the removed tracks are then taken out of an index that has the dimension of the configured embedding provider. If the copy cannot be made, nothing is removed. PlexMix does not delete these copies. Each one is as large as the database, so delete the old ones yourself.

### Changed
- **BREAKING (dev workflow):** `plexmix ui` and the Docker image now serve the production build by default; pass `--dev` for hot reload (`--prod` still works).
- Give the web UI a warmer, more tactile feel: softer corners (12px controls, 18px cards, pill-shaped badges, tabs and filter chips), white cards resting on soft warm shadows that lift on hover, a glowing primary button, and springy hover/press feedback. Reduced-motion preferences still disable all animation.
- Mark the primary dashboard quick action with a warm tint instead of an orange left stripe.
- Radix components (buttons, switches, progress bars, callouts) now use PlexMix's own orange scale, with dark text on orange buttons for legibility.
- Keep you signed in across tabs of the same browser for up to 7 days when a UI password is set.
- Render the changelog on the server so pages no longer download a markdown/KaTeX bundle.
- The Docker health check now waits up to 15 minutes for the web UI build at container start before reporting the container unhealthy.
- Make the cancel-task confirmation a real alert dialog that closes on Escape, focuses Keep running and styles Cancel task as destructive.
- Show each playlist's prompt, date, track count, top artists and its own cover swatch instead of four identical gradients.
- Make the command palette a proper dialog for screen readers and keyboard users: it highlights the command Enter will run, keeps focus inside while open and returns it on close.
- Only offer "Cancel running task" while something is running.
- Remove the "Built with Reflex" badge from the web UI, which covered the Library pager and the Tagging row buttons.
- Show Ctrl K instead of ⌘K outside macOS.
- Enforce the 280-character prompt limit, and show "Describe a vibe first" instead of silently ignoring an empty prompt.
- Status lights now mean 'checked and working', not just 'configured', and use proper provider names.
- Library bulk Tag adds to existing tags by default; replacing them is now an explicit choice.
- Make Library's "Generate embeddings" with nothing selected offer to embed every track that is still missing an embedding, after a confirmation.
- **BREAKING (scripts):** Ask before a sync removes a large number of tracks, and exit with status 1 when a sync did not do its whole job. Check any script or scheduled job that runs `plexmix sync`:
  - When Plex no longer lists more than 50 tracks and more than 5 percent of the library, the sync keeps those tracks. In a terminal it asks "Remove 5,443 of 5,943 tracks?" once the sync has finished. The default answer is No. Smaller removals are applied without a question, as before. After a Yes, PlexMix lists the library again and removes only the confirmed tracks that Plex still does not list. If more tracks are missing by then, it removes nothing and asks you to sync again.
  - Without a terminal (cron, a script, `docker exec` without `-it`) the sync cannot ask. It keeps the tracks, prints how many it kept, and exits with status 1. Add `--allow-removals` to let such a run remove them.
  - A sync also exits with status 1, and prints no "completed successfully" line, in these cases: Plex stops answering or cannot list the library; more than 10 and more than 1 percent of the artists, albums or tracks could not be read; Plex lists 0 tracks while PlexMix holds tracks; or the backup before a large removal fails. Nothing is removed in any of them. Most of these runs used to end with a success line and status 0.
  - The same rules apply to the sync that `plexmix config init` and `plexmix doctor` offer. In the web UI, a sync that held a removal back ends with a warning, on the Dashboard too, and the Library page shows a notice with a **Review removal** button that opens the confirmation. A confirmed removal can be cancelled until its deletes are saved, and a cancelled removal removes nothing. A held removal belongs to its library: after the database path, the Plex address or the Plex library changes in the settings, confirming it removes nothing.

### Fixed
- Fix light mode showing dark-mode orange colors: the active navigation item rendered dark brown and the "PlexMix" wordmark a pale, hard-to-read orange. Dark color values now apply only in dark mode.
- Fix web UI keyboard shortcuts and saved appearance settings, which silently never ran in the default `plexmix ui` dev mode: ⌘K / Ctrl+K, `/`, `g`+key navigation, and Escape to close the command palette now work, Density and Accent intensity are restored after a reload, and the Generator's animated "AI thinking" canvas starts reliably.
- Make the navigation rail, command-palette trigger, and avatar real buttons so they can be reached and used with the keyboard; rail labels also show on keyboard focus.
- Raise text contrast to WCAG AA across the web UI: deeper orange for accent text, darker muted text in light mode and lighter in dark mode, readable text colors for green/yellow/red/blue/purple badges and callouts, and a focus outline that is visible on every surface.
- Stop page loads in another tab from cancelling the other tab's background work; orphaned per-tab work, and any key typed but not saved in a closed Settings tab, is now cleaned up a couple of minutes after its tab disconnects, even when no other tab is open.
- Fix Generator state failing to serialize (`cannot pickle '_thread.lock'`) during playlist generation.
- Stop Settings saves from copying environment-variable secrets into the keyring or config file.
- Stop the sign-in card flashing on page load when no UI password is set.
- Show the loading screen, not a tab's old page content, when it is reloaded after a logout, a password change or an expired session.
- Empty a Settings key field when you switch provider or the page reloads, so a key you typed but did not save is never silently dropped or saved for the wrong provider.
- Show release notes in the in-app changelog on pip installs, with the running version.
- Fix `plexmix ui` failing to find the web app on pip/wheel installs.
- Announce login errors to screen readers and mark the password field invalid.
- Make every Settings section reachable by keyboard and deep-linkable via `?section=`.
- Give every Settings field, switch, slider and select a real label, show the Duration Limit help without hovering, and announce Test/Save results to screen readers.
- Open saved playlists from the keyboard; the playlist view is now a proper dialog with named reorder controls.
- Name the Generator's prompt, sliders, genre and ordering controls, expose the Options state, and announce progress phases and failures.
- Name Library checkboxes and pagination, and make Delete and Regenerate confirmations distinct alert dialogs.
- Label the inline tag editor's Moods, Environments and Instruments fields, keep the track name visible, save with Enter, cancel with Escape and return focus to the row.
- Give Doctor and Dashboard real headings and lists, and announce health-check results.
- Fix the command palette dropping typed characters and running the wrong command on Enter; filtering now happens instantly in the browser.
- Fix "Toggle dark mode" in the command palette, which failed on the server.
- Fix the Generator prompt and Genre field dropping or reordering typed characters on slow connections.
- Fix Library search and genre filters losing typed characters and showing unrelated results.
- Fix low-contrast placeholder text in the Library and Tagging filter fields.
- Fix Tagging and History filter fields losing typed characters, and ignore invalid Stale days values instead of failing silently.
- Fix rerunning a playlist from History, or picking a recent prompt in the command palette, while generated results were on screen: the old tracks were relabelled (and could be saved) under the new prompt instead of the prompt field showing it. While a playlist is being generated, the prompt is now kept and a message says to try again when it finishes.
- Keep a Generator prompt or tag edit you were typing when you leave the page with the browser's Back or Forward button, and keep text typed into a Library filter right after clicking Clear.
- Accept only digits in the Library year and Tagging Stale days fields.
- Show provider failures with their real cause (for example a missing local-model package) instead of 'Check your API keys'.
- Doctor's verdict now matches its rows, shows 'Checking…' instead of false 'Healthy' results, and asks before starting long or cloud-bound fixes.
- Tagging's track count now follows the current filters, and tagging asks for confirmation (including how many existing tags it will replace) before it starts.
- Settings warns before an embedding change breaks your search index, has one Save/Discard bar that stays visible, and no longer shows a blank model or false 'Unsaved changes'.
- History, Library, Tagging, Dashboard and Doctor show loading placeholders instead of empty or zero states while data loads.
- Keep the Library selection bar in view while you scroll, and show how many selected tracks are on other pages.
- Show Plex connection problems in the web UI in plain words with a link to the right Settings section, instead of a terminal command.
- Make the web UI usable at 200% browser zoom and in windows down to 320px wide:
  - The navigation rail scrolls and compacts so every item stays reachable without shifting the page, and its labels are never cut off.
  - The top bar switches to a compact layout so the page name is never hidden behind the search button and no control is pushed off-screen.
  - Dashboard tiles, quick actions and History cards reflow to fewer columns instead of running off the side.
  - The Generator no longer scrolls sideways at high zoom or with long prompts; long prompts wrap to two lines and the full text shows on hover.
  - The Library table scrolls inside its own box instead of silently hiding columns, with a shadow on the side that has more, and long titles wrap inside their cell.
  - Tags in AI Tagging's Recently tagged list are spaced and wrap, so track titles stay readable.
  - The ⌘K palette fits short windows.
  - The Generator's remove-track button shows on touch screens, and hovering a track no longer hides its number.
  - Settings tabs at narrow widths use full section names and no longer hide their scrollbar.
  - Doctor's checks and the Library's action bar, filters and pagination wrap too, so every page reflows at 320px without sideways scrolling.
- Keep the Dashboard status strip's Plex, AI and Embeddings readouts on one row on wide windows: a long cause such as "Can't reach your Plex server at …" now wraps inside its readout instead of pushing the next one onto a row of its own. Narrower windows reflow them into even columns.
- Stop the "Built with Reflex" badge in the bottom-right corner (shown by the default production build) from covering the Library selection bar's Tag, Delete and Clear buttons and the Settings Discard and Save buttons.
- Keep the table row or Settings field you just used in view when the Library selection bar or the Settings save bar appears over it, and stop keyboard focus from landing behind either bar.
- Fix the Generator's readiness line saying a prompt is sent to "Custom (OpenAI-compatible)" when the Custom embedding endpoint runs on this machine: a localhost endpoint now reads "prompt stays on this machine" and a remote one is named by its host, as in the job confirmations.
- Remove the repeated "MusicBrainz" provider line from Doctor's MusicBrainz enrichment confirmation, which screen readers read as "MusicBrainz MusicBrainz".
- Give the Tagging "Untagged only" and Library "Has audio analysis" checkboxes the same 2px focus ring as every other control, instead of the browser's thin default.
- Keep tracks that a sync could not read from Plex. A sync used to remove every track whose details it failed to read, together with its tags, embedding and playlist entries, and then report success. In a test against a stand-in Plex server, an outage of 1 second during a sync removed 2,449 of 5,943 tracks. A sync now removes a track only when Plex's own list of the library no longer contains it. A track that Plex listed but that could not be read is kept as it is, and the sync says how many were kept. When Plex stops answering during a sync, or cannot list the library, the sync stops before it changes anything and says so in one sentence. A request that fails because the connection was lost is tried once more after 1 second before the sync stops.
- Keep a track's artist and album when the artist or the album could not be read from Plex. Such a track could be moved to "Unknown Artist" or "Unknown Album", and later syncs did not move it back. An album keeps its artist in the same way. A new track that is filed under "Unknown Artist" because its artist is new to PlexMix and could not be read is moved to its artist by the next sync that reads it.
- End `plexmix sync` with one sentence and status 1 when the connection check at the start of a sync fails, for example when Plex stops answering right after the command has connected. The sentence ends with "Nothing was changed." The command ended in an error report.
- Apply a removal whole or not at all. Ctrl+C during a sync's removal could keep some of the removals while the sync history recorded none. An interrupted removal is now undone, and a removal that was saved is recorded in the sync history and printed even when Ctrl+C follows it at once.
- Report a Plex server that stops answering while the music library is selected as unreachable. `plexmix sync` and the web UI said the library "may have been renamed or removed".
- Print what a sync had already added, updated and removed, and where the backup of a large removal is, when the sync is interrupted or a later stage fails (for example a lost connection to the embedding provider). Such a run printed none of this.
- Count the tracks that a failed sync had already added, updated and removed in its sync history row. The row said 0 for all three.

### Security
- Stop sending stored Plex tokens and API keys to the browser; Settings now shows where each secret is stored and accepts replacements only.
- Stop Settings saves from writing `PLEXMIX_UI_PASSWORD`, `AI_CUSTOM_API_KEY`, `AI_LOCAL_AUTH_TOKEN` or `EMBEDDING_CUSTOM_API_KEY` from the environment into `config.yaml`, where a copied UI password kept working after you changed the variable.
- Keep stored secrets out of Settings error messages and server logs when `config.yaml` holds an invalid value.
- Make the web UI's session files (`.states/`) readable only by your user account, since they hold the sign-in session.
- Remove existing `.states` pickles on the next `plexmix ui` launch: earlier versions could save Settings secrets in these web UI state files (`.states/*.pkl` in the web app directory). Reflex clears that folder every time the UI starts, so upgrading and launching once is enough; if you copied the app directory elsewhere, delete its `.states/` folder yourself.
- Stop printing the values of variables in CLI error reports. When a command failed with an unexpected error, the report could show the Plex token, API keys and the web UI password.
- Stop repeating a rejected value in settings errors. A wrong value in `config.yaml` or in a settings environment variable is reported by the name of the setting or variable (for example `ui.password` or `AUDIO_WORKERS`), and a `config.yaml` that cannot be parsed is reported by file, line and column. An unknown key that does not look like a setting name is reported as "unknown key" and not repeated, because a missing space after a colon (`ui: {password:secret}`) turns the value into part of the key.
- Mask a user name and password embedded in the Plex URL in `plexmix config show`, `plexmix config test` and the log: `http://user:password@host:32400` is shown as `http://***@host:32400`. This also holds when the scheme is mistyped (`http:/`, `http//`) or missing, and when the password contains a space. A Plex token pasted into the URL as `?X-Plex-Token=...` is masked the same way. The saved setting is not changed. Stop printing the length of the Plex token in `config test` and in the log.
- Stop `plexmix sync` printing the Plex URL with its user name and password when Plex connects but cannot list the library: the error report now shows the masked text.
- Report a Plex URL that cannot be read as a URL with one fixed sentence ("the Plex server address is not a valid URL"). The HTTP library's own message quoted the user name and the start of the password when the password contained `/`, `?`, `#` or `\`.
- Refuse a Plex token that cannot be sent in a request (one with a space or a line break in the middle, or a non-ASCII character) before a command or a sync connects to Plex. The "Plex token contains invalid characters" message does not show the token. Refuse a local LLM auth token of that kind too when the local provider uses an endpoint: the provider is not started with it, and "Local endpoint auth token contains invalid characters" is logged without the token.

## [0.12.0] - 2026-07-12

### Security
- Enforce the web UI password on the server, not just in rendering. Reflex exposes every public state handler over the websocket, so previously an unauthenticated client on a network-exposed instance could invoke handlers directly to read the Plex token and all API keys (via the settings loader) or trigger destructive actions. Sensitive handlers (settings load/save, sync, embedding generation, tagging, Doctor fixes, track/playlist deletion, playlist export) now reject unauthenticated requests.
- Store the Plex token and custom API keys in the system keyring first when saving settings from the UI, writing them to `config.yaml` only as a fallback when the keyring is unavailable — the UI no longer duplicates secrets into a plaintext file when keyring storage succeeds.

### Fixed
- Fix the web UI's live progress and several dead controls. Long-running jobs (sync, embeddings, tagging, MusicBrainz, audio, Doctor fixes) now update their progress bars live and report completion instead of appearing frozen; the bulk Tag/Delete buttons open working dialogs; the sync mode selector offers a confirmable "Regenerate" option; History playlists can be deleted, reordered, and exported to JSON; the Changelog dialog can be opened; and the "Generate embeddings" button gives feedback when no tracks are selected. Progress state is also shared correctly across multiple open browser tabs.
- Fix Reflex event-handling bugs that froze the backend or raised errors: connection tests, saving playlists to Plex, the Doctor health scan, and local-model construction now run their blocking work off the event loop; inline tag edits and bulk actions no longer raise state-mutation errors; the Generator "Regenerate" and "Tag all" actions no longer crash; and cancelling a sync now ends cleanly instead of wedging the task as "running".
- Fix generator/history flows: playlist generation no longer crashes on a track with an unknown duration; a double-click can't start two overlapping generations; each generated playlist is saved under its own name; saving no longer flips the view back to the generating state; reordering a playlist that contains the same track twice no longer fails; and exporting to Plex from History reports failure honestly instead of always showing success.
- Fix a login crash on passwords containing non-ASCII characters, and a command-palette bug where Enter could trigger a stale item after the query changed.
- Fix embedding search corruption when switching embedding provider or model. Embedding reads, existence checks, and FAISS index rebuilds are now scoped to the active model, so a provider switch re-embeds tracks and rebuilds cleanly instead of mixing dimensions (which crashed the index build) or skipping re-embedding. Selecting a specific cloud model now reports its true dimension (e.g. OpenAI `text-embedding-3-large` is 3072, not 1536), and the library view no longer duplicates rows for tracks that have embeddings from more than one model.
- The web UI "Generate embeddings" action now updates the FAISS search index (previously it wrote embeddings to the database but never rebuilt the index, so the new embeddings were invisible to search).
- Fix audio feature analysis: energy is now computed as RMS so it produces meaningful values (previously nearly every track was classified "low" energy and "high" was unreachable), danceability is normalized to a 0–1 scale to match how it's used, audio analysis during a UI sync now actually saves its results, and CLI sync analyzes audio before generating embeddings so the embeddings can incorporate audio features.
- Building an AI provider for a non-default provider no longer sends the default provider's model name (e.g. a Gemini model to OpenAI); each provider falls back to its own default model.
- Write the FAISS index and its metadata atomically, so an interrupted save can't corrupt the index (which recovery would then delete). A regenerate that fails to connect, or is cancelled immediately, no longer wipes all tags and embeddings before validating. Interrupting embedding generation, and removing tracks during sync, now keep the index and database consistent. Regenerating embeddings from the CLI/UI includes audio-feature text like sync does, and `embeddings generate` rebuilds the index even when nothing new needs embedding so a lost index recovers.
- Fix the setup wizard and configuration handling: re-running the wizard preserves settings it doesn't manage (audio path prefixes, MusicBrainz email, UI password, database path); it selects the right embedding model/dimension for the chosen provider instead of leaving the Gemini default; it validates the library selection; the Plex token is recoverable on systems without a keyring; `AI_`/`EMBEDDING_`/`LOG_`/`PLAYLIST_`/`DATABASE_` environment variables now override the config file; Windows-style Plex paths are remapped by the audio path prefix; and config saves are atomic and written with owner-only permissions.
- Fix playlist generation and export: mood/environment/instrument filters match whole tags (so "work" no longer matches "workout"), selective filters no longer starve the candidate pool, identically-titled albums by different artists are no longer merged by the diversity cap, large candidate pools no longer exceed SQLite's parameter limit, invalid shuffle modes warn instead of being ignored, a blank playlist name fails cleanly, malformed import files report a clear error, and CLI playlist creation no longer reports success when the Plex playlist wasn't created.
- Fix local model lifecycle: switching embedding models no longer leaks worker processes, concurrent startups don't spawn duplicate workers, a crashed worker no longer hangs the app, and the UI "Download Model" works for the default (and other bare-named) sentence-transformers models.
- Fix packaging and deployment: the PyPI sdist/wheel now include the Reflex UI files, `docker-compose` `PLEX_URL` is overridable and resolves on Linux, and `.dockerignore` excludes virtualenvs so Docker builds don't clobber the container's environment.
- Fix MusicBrainz enrichment: titles/artists with quotes or backslashes no longer break the search (which caused false "no match" results cached for 90 days), album release-group IDs are now actually fetched and stored, and enrichment progress reaches 100% instead of stalling when some tracks have no match.
- Fix tagging defects: interrupting AI tagging now saves the tags generated so far (instead of discarding them despite the "progress saved" message); CLI tagging stamps a tag timestamp so tagged tracks aren't seen as permanently stale, regenerates embeddings for every tagged track (not just the last batch), and skips empty results; the local model honors its configured output-token budget so large batch responses aren't truncated; a crashed local model worker no longer hangs; and Doctor's force-retag no longer crashes immediately.
- Fix AI provider defects: permanent quota/billing exhaustion is treated as a fatal error (not retried away, which made tagging silently "succeed" with no tags); Gemini honors the request timeout and no longer emits the literal text "None" on odd responses; the Claude sampling-parameter check no longer misreads a date-stamped Opus 4.0 model id; the builtin local-LLM model ids point at real Hugging Face repos so local mode can initialize; Claude is recognized (not just "anthropic") when checking configuration and testing the provider; the keyless custom provider is no longer blocked by a required-key check in `tags generate`; and provider construction failures degrade gracefully instead of crashing `plexmix sync`.
- Fix several CLI commands that ignored configuration or Typer option defaults: `sync full`, the setup wizard's initial sync, and Doctor's sync no longer force-enable audio analysis (and MusicBrainz) that you didn't ask for; `--musicbrainz` now actually enriches; `config test`, `db info`, and `db reset` read your saved config file (custom paths included); the global `--config` option is now honored; the log file location respects `PLEXMIX_DATA_DIR`/`LOG_FILE_PATH`; a malformed `PLEXMIX_UI_PORT` no longer crashes the whole CLI; `doctor --force` no longer crashes after deleting tags/embeddings; and `plexmix ui` reports a real exit code and a clear error instead of silently doing nothing.
- Fix a crash on startup for databases created by older PlexMix releases. Database open ran index creation before schema migrations, so any pre-existing database that the migrations were meant to upgrade (missing the `audio_features` table, or using the older `environment`/`primary_instrument` columns) raised an error and could not be opened. Migrations now run first, and the schema-version table is created up front.
- Fix library search silently dropping tracks whose title, artist, or album contains an apostrophe, hyphen, ampersand, or period (e.g. "Don't Stop Believin'"). The full-text search now escapes the query instead of passing raw text to FTS5.
- Sync now propagates Plex metadata edits to existing artists and albums (renames, genre/year/cover changes) instead of only writing them for newly discovered items, and detects relocated/renamed track files so audio analysis picks up the new path.
- Albums with an out-of-range year (e.g. 0 from Plex) now import with an empty year instead of being skipped and having their tracks reassigned to "Unknown Album".
- "Recently tagged" now orders by tag time instead of insertion id, so freshly retagged tracks actually appear as recent.
- Corrupted-database recovery now writes a uniquely named, timestamped backup instead of a single fixed filename, so repeated recovery no longer overwrites the previous backup.
- Prevent sync from silently deleting the entire local library (tracks, embeddings, tags, audio features, playlist contents) when a Plex fetch fails or returns nothing. A transient network error, a renamed/unselected music library, or tracks rated above 2.5 stars could each make sync treat the library as empty and remove everything while reporting success. Fetch failures now abort the sync, an unselected library raises instead of yielding nothing, and the deletion pass is skipped when Plex returns zero tracks against a populated database.
- Convert Plex's 0–10 `userRating` to PlexMix's 0–5 scale when reading tracks. Previously any track rated 3 stars or higher failed validation, was skipped, and was then deleted from the database on the next sync.
- Fix web UI sync, embedding generation, batch tagging, and all Doctor repair actions, which always crashed with a SQLite cross-thread error. The database connection is created on the event-loop thread and used from a worker thread; it is now opened with `check_same_thread=False` for that sequential handoff.

## [0.11.0] - 2026-06-01

### Added
- Redesign the web UI around a glass icon-rail and a ⌘K command palette (search pages, run actions, fire a quick vibe; `g`+key navigation). The Generator is reimagined as an "AI-thinking" showpiece: a live particle vector-field, a four-phase progress tracker bound to the real generation pipeline, a streaming terminal log, and a results playlist with removable tracks.
- Add a Settings → Appearance section with a theme toggle plus Density (comfortable/compact) and Accent-intensity (subtle/balanced/vivid) preferences, persisted across reloads.
- Allow cancelling an in-progress playlist generation from the Generator.
- Cancel any running long-running job from the UI — library sync, embedding generation, MusicBrainz enrichment, audio analysis, Doctor fixes, and tagging — each behind a confirmation, with the work actually stopping and a "cancelled" outcome shown. Adds a global **"Cancel running task"** command (⌘K) that stops everything in flight.

### Changed
- **The web UI now defaults to light mode** (previously dark). Your choice is remembered and can be toggled from the rail, the top bar, or Settings → Appearance.
- Refresh default AI models to the current generation in a balanced, cost-aware tier: Gemini `gemini-3.5-flash`, OpenAI `gpt-5.4-mini`, Claude `claude-sonnet-4-6`, Cohere `command-a-03-2025`. Newer flagship options (`gpt-5.5`, `claude-opus-4-8`, `gemini-3.1-pro-preview`, `command-a-plus-05-2026`) are also selectable. Embedding models are unchanged, so existing FAISS indexes do not need to be regenerated.
- The configured `temperature` setting now has no effect for OpenAI GPT-5, Gemini 3, and Claude Opus 4.7+ models — those families don't accept a custom temperature. It still applies to Claude Sonnet/Haiku, Cohere, custom, and local providers.

### Fixed
- OpenAI GPT-5 requests now use `max_completion_tokens` and omit the unsupported `temperature` (GPT-5/o-series reject the old `max_tokens` + custom-temperature combination), so OpenAI playlist generation and the Settings "Test OpenAI" button no longer fail with a 400.
- Gemini 3 requests now constrain thinking (`thinking_level=low`) and use the model's default temperature, so responses aren't starved of output tokens or pushed into low-temperature loops.
- Claude Opus 4.7+/4.8 requests now omit sampling parameters (`temperature`/`top_p`/`top_k`), which those models reject with a 400; Sonnet and Haiku continue to honor `temperature`.

### Removed
- The end-of-life `gemini-2.0-flash-001` model (Google shutdown 2026-06-01) and the invalid `claude-opus-4-1-20250414` model ID.

## [0.10.0] - 2026-05-28

### Added
- Sanitize and validate API keys across all sources (keyring, environment variables, and saved settings): surrounding whitespace and newlines are stripped, and a key containing characters that cannot be sent over HTTP now fails fast with a clear "re-enter your key" message instead of a cryptic error mid-run.

### Fixed
- Stop AI tag generation immediately on an unrecoverable provider error (e.g. an invalid API key or an HTTP-edge/proxy rejection) instead of retrying every batch and writing empty tags across the whole library; the failure is reported once in plain language and any tags generated beforehand are kept.
- Translate opaque provider errors — including the HTML "Error 400 (Bad Request)" page returned by an API gateway or proxy — into an actionable message pointing at the API key or network path.

## [0.9.0] - 2026-03-16

### Added
- MusicBrainz integration for community-curated genre enrichment, canonical artist IDs (MBIDs), and recording type detection (live, remix, cover, etc.)
- `musicbrainzngs` as a required dependency (no separate install needed)
- CLI commands: `plexmix musicbrainz enrich`, `plexmix musicbrainz info`, `plexmix musicbrainz clear-cache`
- `--musicbrainz` flag on `plexmix sync` to enrich during sync
- MusicBrainz settings tab in web UI (enable/disable, confidence threshold slider, contact email)
- "Enrich with MusicBrainz" button on Library page with progress tracking
- MusicBrainz genres and recording type feed into embedding text for better semantic search
- MusicBrainz genres included in AI tag generation prompts for improved accuracy
- Artist MBID-based deduplication in playlist generation (handles name variants)
- SQLite cache table for MusicBrainz lookups (90-day TTL, avoids re-fetching)
- Dashboard and Doctor page stats for MusicBrainz-enriched tracks
- MusicBrainz enrichment detection and fix on Doctor page
- "Regenerate All" tags button with confirmation modal on Doctor page
- AGPLv3 license
- 61 new tests: embedding pipeline (32) and sync error recovery (29)
- 29 new MusicBrainz tests covering client, service, cache, and database

### Changed
- License changed from MIT to AGPL-3.0-only
- Last sync time displayed in human-readable 24hr format (e.g., "Mar 14, 2026 16:50")
- `musicbrainzngs` moved from optional to required dependency

### Fixed
- Changelog not loading in Docker (added `!CHANGELOG.md` to `.dockerignore`)
- MusicBrainz transient API errors no longer cached as definitive no-match results
- SQLite thread-affinity in MusicBrainz enrichment (DB created inside executor thread)
- MusicBrainz enrichment gated on `enabled` setting in sync pipeline and UI

## [0.8.5] - 2026-03-16

### Added
- Clickable version badge in sidebar opens a changelog modal with full release history rendered as markdown

### Fixed
- Fix KeyError 'id' when clicking Edit on the tagging page (pass individual fields instead of whole dict to avoid Reflex serialization issue)
- Fix unreadable text when hovering over table rows (explicit text color on hover with proper CSS specificity)

## [0.8.4] - 2026-03-16

### Fixed
- Fix Docker "latest" tag pointing to -local image instead of slim (default) image by reordering CI jobs so the slim image is pushed last

## [0.8.3] - 2026-03-15

### Fixed
- Fix frontend freezing during tag generation, embedding generation, and library sync by wrapping blocking API calls and CPU-bound work in `run_in_executor` so the asyncio event loop stays responsive for WebSocket heartbeats
- Wrap sync engine operations (`incremental_sync`, `regenerate_sync`) in executor
- Wrap embedding generation API calls in executor (library and doctor pages)
- Wrap tag generation API calls + `time.sleep` retries in executor (tagging and doctor pages)
- Replace deprecated `asyncio.get_event_loop()` with `asyncio.get_running_loop()`

## [0.8.2] - 2026-03-15

### Fixed
- Fix SyntaxError in dashboard page (keyword argument before positional arguments)

## [0.8.1] - 2026-03-15

### Added
- App version number displayed in sidebar navigation (desktop and mobile)

### Fixed
- Dashboard showing 0/0/0/0 stats and "Configure" on fresh page load — added `on_mount` fallback for when `on_load` doesn't fire after the index page redirect
- Last Sync now uses `sync_history` table instead of `MAX(last_played)` which was always NULL for users who sync but don't play through Plex
- Mypy errors for optional dependency imports (reflex, torch, transformers, sentence-transformers)
- Ruff lint warnings for unused imports and variables

## [0.8.0] - 2026-03-15

### Added
- Persistent `TaskStore` architecture for background tasks — sync, tagging, audio analysis, embeddings, and doctor fixes survive browser disconnects and reconnects
- Client-pull polling via hidden DOM buttons + `setInterval` replaces server-push progress updates, eliminating "disconnected client" warnings
- `TaskEntry` dataclass with status tracking (running/completed/failed/cancelled), progress, messages, and extensible `extra` dict
- Global exclusivity per job type prevents concurrent DB-contending operations (e.g., two syncs)
- Multi-user forward compatibility: TaskStore keyed by `(user_id, job_type)` with `"default"` for single-user mode
- `poll_task_progress()` handlers on library, tagging, and doctor pages for session recovery on page load
- Shared service modules: `sync_service.py`, `playlist_service.py`, `tagging_service.py`, `audio_service.py`
- Centralized constants module (`config/constants.py`) for batch sizes, pagination, retry defaults, and diversity constraints
- Reusable form field components (`ui/utils/form_utils.py`): `form_field`, `year_range_field`, `help_text`
- 29 new tests for `TaskStore` and `JobManager` lifecycle, progress, cancel/pause, and exclusivity

### Changed
- Refactor library, tagging, and doctor states to write progress to `TaskStore` instead of `async with self:` inside loops
- Refactor CLI commands (sync, create, tags, embeddings, audio) to use shared service layer — removes duplicated provider/connection setup
- Refactor UI states (library, generator, tagging) to use shared service layer and centralized constants
- Split `SettingsState` (1,068 → 638 lines) into core state + `_settings_testing.py` + `_settings_downloads.py`
- Split `settings.py` page (1,045 → 364 lines) into core page + `_settings_sections.py`
- Cancel/pause/resume operations no longer require client token — work directly on TaskStore by job type

## [0.7.0] - 2026-03-14

### Added
- Shared service layer (`services/providers.py`) centralizing provider construction and credential resolution across CLI and UI
- Unified provider registry (`services/registry.py`) — single source of truth for all provider model lists, default models, embedding dimensions, and API key requirements
- Reusable `stat_tile` component and `str_dict`/`format_eta` helpers extracted from duplicated code
- CI pipeline hardening: Black, Ruff, and Mypy checks in GitHub Actions; 35% coverage threshold
- 204 new tests (520 total, 47% coverage): registry, Plex client, doctor/config CLI, and database playlist/FTS tests
- Playlist export/import CLI commands (`plexmix playlist export/import/list`) with JSON and M3U format support
- JSON export button on History page (card + detail modal)
- Smart shuffle & ordering: 4 track ordering modes (`similarity`, `random`, `alternating_artists`, `energy_curve`)
- Energy curve algorithm orders tracks in an arc shape using audio features (energy, danceability, tempo)
- Alternating artists algorithm maximizes artist diversity via round-robin interleaving
- Shuffle mode selector on Generator page and `--shuffle` CLI option on `plexmix create`
- Playlist templates with database storage, CRUD API, and 5 built-in presets (Morning Commute, Workout, Study Session, Dinner Party, Late Night)
- Template gallery on Generator page: horizontally scrollable cards, one-click apply, save current config as template
- Import modal on History page with file upload supporting JSON and M3U formats with auto-detection and track matching
- Centralized job manager (`ui/job_manager.py`) replacing scattered module-global dicts for background task lifecycle management
- Versioned schema migration system with `schema_version` table replacing ad-hoc migration checks
- Provider auto-discovery for Ollama and OpenAI-compatible endpoints (supports `/v1/models` and `/api/tags`)
- "Discover Models" button on Settings page for custom and local provider endpoint modes with clickable model badges
- Smarter tag regeneration: `tags_generated_at` timestamp on tracks, `--retag-stale DAYS` CLI option, stale days filter on Tagging page
- Incremental FAISS index updates via `IndexIDMap`: `update_vectors()` and `remove_vectors()` without full rebuild; auto-migrates legacy `IndexFlatIP` indexes
- Streaming SSE responses for local LLM endpoint mode (Ollama/LM Studio) with non-streaming fallback
- Plex token pre-flight validation before sync operations with clear error messages for expired/invalid tokens
- Duplicate avoidance: `--avoid-recent N` CLI option and "Avoid Recent" input on Generator page to exclude tracks from recent playlists
- `EmbeddingGenerator.verify_dimension()` method for runtime dimension auto-detection
- Bulk operations in Library: "Apply Tags" dialog and "Delete Selected" with confirmation for multi-selected tracks
- Playlist track reordering in History detail view with move up/down buttons and persistent save
- Global keyboard shortcuts: vim-style `g+key` navigation (d=dashboard, g=generator, l=library, etc.), `/` to focus search, `Esc` to blur
- Library drill-down filters: filter by tag substring and "has audio features" checkbox
- Playlist rerun from History: full generation config stored with saved playlists, "Rerun" button loads all parameters into Generator
- `generation_config` JSON column on playlists table (Migration 9) for preserving generation parameters
- Mobile-responsive button labels: icon-only on mobile for Library floating bar, Generator action bar, and History detail modal

### Changed
- Split `cli/main.py` (1,579 lines) into 9 focused command modules (largest is 305 lines)
- Refactor CLI, library state, tagging state, generator state, doctor state, and settings state to use shared provider service
- Replace hardcoded provider/model lists in `ai/__init__.py`, `config/settings.py`, `ui/states/settings_state.py`, and `services/providers.py` with registry lookups
- Delete dead `ui/app.py` entrypoint (consolidated into `plexmix_ui/plexmix_ui.py`)
- Refactor `library_state.py` and `tagging_state.py` to use centralized job manager (removed 5 module-global dicts, 4 cleanup functions, 2 atexit registrations)

### Fixed
- Fix undefined `embedding_provider` variable in create playlist command
- Fix CLI help failing in restricted environments (best-effort file logging)
- Fix 171 mypy errors across 28 core source files
- Clean all Ruff lint warnings in source and test files

## [0.6.7] - 2026-03-13

### Added
- Parallel audio analysis with configurable worker count (`AUDIO_WORKERS`, default 4)
- Concurrent sliding-window pattern for library, sync, and doctor audio analysis flows

### Changed
- Audio analysis now processes multiple tracks simultaneously via ThreadPoolExecutor

## [0.6.6] - 2026-03-13

### Added
- Pause/resume/stop controls for audio analysis on the library page
- ETA calculation displayed during audio analysis (adapts after pause/resume)
- Confirmation dialog before stopping audio analysis (progress is preserved)

## [0.6.5] - 2026-03-13

### Fixed
- Fix environment variables (e.g. `AUDIO_PATH_PREFIX_FROM`) being ignored after config.yaml is created
- Add `_apply_env_overrides()` so Docker env vars always override saved YAML config values

### Added
- Run audio analysis automatically after library sync when `AUDIO_ANALYZE_ON_SYNC=true`
- Audio analysis progress updates and cancellation support in UI sync flow

## [0.6.4] - 2026-03-13

### Fixed
- Validate Plex connect() and select_library() return values in generator and history states
- Fix `exporting` flag never reset on error in history_state export_to_plex
- Fix unchecked Plex connection failures causing silent crashes in save-to-Plex and export flows

## [0.6.3] - 2026-03-13

### Fixed
- Fix library sync indexing 0 tracks — `select_library()` was never called after connecting to Plex
- Fix dashboard stats crash (`Path(None)`) when `faiss_index_path` is not explicitly set
- Fix generator using raw `faiss_index_path` (None) instead of `get_index_path()` with proper default

## [0.6.2] - 2026-03-13

### Fixed
- Fix "sqlite3.Connection object attribute 'commit' is read-only" crash on library regenerate (Python 3.12+)
- Replace monkey-patching of `conn.commit` in `deferred_commits()` with flag-based `_commit()` helper
- Fix potential stuck-flag edge case if `BEGIN` transaction fails inside `deferred_commits()`
- Reset `_defer_commits` flag on connection close to prevent state leaking across reconnects

## [0.6.1] - 2026-03-13

### Fixed
- Fix tagging page React `useEffect` null crash caused by double-wrapped accordion components
- Fix config.yaml not persisting across Docker restarts — now saved to persistent volume (`PLEXMIX_DATA_DIR`)
- Fix Plex token lost after page refresh — token now persisted to config.yaml as keyring fallback
- Fix API keys from environment variables not loading when switching providers in Settings
- Fix generator page accordion using same double-wrapped pattern (preventive)

### Changed
- Accordion items pass raw content to `header=`/`content=` instead of pre-wrapped subcomponents
- Provider switch in Settings now auto-loads API key from env vars/keyring for the new provider
- Settings `load_from_file`/`save_to_file` use `PLEXMIX_DATA_DIR` with backwards-compat legacy fallback
- README screenshot presentation improved with browser chrome frames

## [0.6.0] - 2026-03-13

### Changed
- Complete UI revamp across all pages: dashboard, settings, library, generator, history, tagging, and doctor
- Settings page redesigned with vertical sidebar navigation and section icons (replacing tabs)
- Generator page redesigned with centered hero layout, "What's the vibe?" textarea, and mood pill suggestions
- Library page redesigned with separated command bar, horizontal filter row, and floating bulk actions bar
- History page redesigned with clean list rows replacing card grid
- Tagging page redesigned with accordion filters and switch toggles
- Doctor page redesigned with slim health banner and dashboard-style stat tiles
- Track table: embedded column shows compact dot indicator; tags column shows max 3 badges with "+N" overflow
- Loading components use CSS shimmer skeleton animation
- Progress modal uses monospace percentage display
- Empty states enlarged with fade-in animation
- New CSS design tokens: glass morphism, focus ring glow, animation classes (fade-in, slide-up, scale-in, spin)
- Updated README screenshots to reflect the revamped UI (dark + light mode)

### Fixed
- Settings error messages now show user-friendly colored banners instead of raw exception text with HTTP headers
- Settings `set_active_tab` explicit method fixes Reflex 0.8.9 deprecation warning for auto-generated setters

## [0.5.8] - 2026-03-13

### Added
- Custom model name input for all AI and embedding providers — type any model name to override the dropdown
- "Tracks with Audio Analysis" stat card on Database Doctor page

### Changed
- Gemini settings tests migrated from deprecated `google.generativeai` to `google.genai` (matching actual providers)
- Cohere AI model list updated: removed deprecated `command`/`command-light`, added `command-r7b-12-2024`, `command-r-plus`, `command-a-03-2025`
- Cohere embedding default model ID corrected from `embed-v4` to `embed-v4.0`
- Cohere embedding model list now includes `embed-v4.0`
- Cohere `output_dimension` only sent for v4+ models (v3 models don't support it)

### Fixed
- Fix Cohere AI and embedding test methods to use `ClientV2` API matching actual providers
- Fix dashboard "Configured" status checking any API key instead of only the selected provider's key
- Fix dashboard AI Provider card not showing custom model name
- Fix Gemini "Test Embeddings" failing in Docker with "No module named 'google.generativeai'"

## [0.5.7] - 2026-03-13

### Fixed
- Fix Docker healthcheck always reporting unhealthy — Reflex backend returns 404 on `/`, use `/ping` endpoint instead
- Fix React `useEffect` null crash caused by `rx.select.item` with reactive `disabled` prop — revert to simple `rx.select` with display name mapping
- Increase healthcheck `start-period` from 30s to 60s to allow Reflex compilation time

## [0.5.6] - 2026-03-13

### Added
- Configurable Reflex ports via `PLEXMIX_UI_PORT`, `PLEXMIX_BACKEND_PORT`, and `PLEXMIX_API_URL` environment variables for Docker port mapping
- `PLEXMIX_ALLOWED_HOSTS` environment variable for custom domain access behind reverse proxies (e.g., `plexmix.example.com`)
- `--backend-port` and `--api-url` CLI options for `plexmix ui` command
- Grey out local AI/embedding options in Settings UI when `sentence-transformers` is not installed (slim Docker image)
- Audio filters in Generator conditionally shown only when audio analysis data exists
- Essentia audio analysis support in Docker (best-effort install, available on amd64)

### Fixed
- Fix Docker `:latest` tag pointing to local image variant instead of slim — split CI into sequential jobs
- Fix generator page crash caused by Radix Select.Item rejecting empty string values
- Fix `DatabaseSettings` Pydantic v2 validation error (`str` field with `None` default)
- Fix blank Database Path and FAISS Index Path in Settings Advanced tab when config hasn't been saved
- Fix websocket connection failures when Docker ports are mapped to non-default host ports

## [0.5.5] - 2026-03-12

### Fixed
- Fix Reflex UntypedVarError crash in Docker: use `dict[str, str]` instead of `Dict[str, Any]` for all state vars used in `rx.foreach`
- Fix Reflex Optional deprecation warnings for year filter state vars
- Fix `has_embedding` badge showing incorrect status after string coercion

## [0.5.4] - 2026-03-12

### Added
- Custom OpenAI-compatible endpoint support for both AI and embedding providers
  - Works with Ollama, LM Studio, vLLM, Together AI, Groq, Fireworks, and any OpenAI-compatible API
  - Configurable endpoint URL, model name, API key (optional), and embedding dimension
  - Full UI settings page support with test connectivity buttons
- `CUSTOM_AI_API_KEY` and `CUSTOM_EMBEDDING_API_KEY` environment variable support
- `_build_embedding_generator()` CLI helper to centralize embedding provider construction

### Fixed
- Add `unzip` to Docker runtime image (required by Reflex to install Bun)

## [0.5.3] - 2026-03-12

### Added
- Add dual-variant Docker builds: slim (`:latest`, ~434MB) and local (`:latest-local`, ~1.1GB with PyTorch)
- Add `[local]` optional extra for sentence-transformers and PyTorch (`pip install "plexmix[local]"`)
- Add `WITH_LOCAL` build arg to Dockerfile for toggling local AI dependencies

### Changed
- Bump numpy from `^1.26.0` to `^2.0.0` (fixes faiss-cpu `numpy.distutils` issue on Python 3.12 arm64)
- Multi-stage Dockerfile reduces default image from 9.4GB to 434MB
- Move sentence-transformers from required dependency to optional `[local]` extra
- CI matrix builds and pushes both image variants on every release tag
- Expand `.dockerignore` to exclude tests, docs, `.github/`, and coverage artifacts
- Document Docker image variants and offline installation in README

## [0.5.2] - 2026-03-12

### Added
- Add missing DB indexes on tracks.file_path, tags, environments, and instruments columns
- Add FTS5 full-text search for library search (replaces LIKE %query% scans)
- Add `insert_embeddings_batch()` and `add_tracks_to_playlist()` with atomic batch writes
- Add `deferred_commits()` context manager for wrapping sync in a single transaction
- Add `file_path` to playlist track data and M3U export with `resolve_path()` for Docker/remote path remapping
- Add `audio_features` to database recovery required tables list
- Add genre cache to avoid repeated DB lookups during sync
- Add `get_track_ids_with_embeddings()` for bulk embedding existence checks
- Add sample `docker-compose.yml` to README

### Changed
- Use Gemini native batch embedding API instead of per-text loop with sleep
- Replace Plex API calls (`artist()`, `album()`) with `parentRatingKey`/`grandparentRatingKey` attribute access to eliminate N+1 HTTP roundtrips
- Convert FAISS search `track_id_filter` to set for O(1) lookups with early return on empty filter
- Use `update_track_tags()` instead of full `insert_track()` for tag saves
- Collect embedding vectors in-memory during sync for FAISS instead of re-fetching from DB
- Run `_create_indexes()` and `_create_fts_table()` on existing DB connect for seamless upgrades
- Update stale `get_max_candidates` documentation reference in CLAUDE.md

### Removed
- Remove unused `generate_playlist()`, `get_max_candidates()`, `_prepare_prompt()`, `_parse_response()`, `_validate_selections()` from all AI providers and base class (dead code — playlist generation uses vector similarity search)
- Remove dead `_sync_artists`, `_sync_albums`, `_sync_tracks` methods from sync engine
- Remove dead `_call_endpoint` and `_generate_with_worker` methods from local provider

### Fixed
- Fix `VectorIndex.search()` treating empty filter list as no filter (`if track_id_filter:` → `if track_id_filter is not None:`)
- Fix FTS query generating invalid MATCH for whitespace-only input
- Fix batch insert atomicity with explicit BEGIN/COMMIT/ROLLBACK transactions

## [0.5.1] - 2026-03-11

### Added
- Add optional password protection for the web UI (`PLEXMIX_UI_PASSWORD` env var)
- Add clickable column sorting (title, artist, album, genre, year) to the Library track table
- Add search filter to Playlist History page
- Add confirmation dialog before "Tag All Untagged Tracks" action with untagged track count
- Add auto-scroll to top on pagination (next/previous/go-to-page and sort changes)
- Add auto-dismiss of generation success messages after 5 seconds
- Add skeleton table loader on Library page during initial load
- Add unsaved changes warning (yellow callout + yellow Save buttons) on Settings page
- Add cancel confirmation dialog before cancelling a sync operation
- Add fade-in CSS transition animations for progress sections and data tables
- Add clickable mood example chips (badges) above textarea in Playlist Generator
- Add "General Filters" and "Audio Filters" labeled groups with divider in Generator advanced options

### Changed
- Replace `print()` statements with proper `logging` module calls in all UI state handlers
- Display tags as colored badge pills instead of raw comma-separated strings in Library and Tagging pages
- Use human-friendly sort labels ("Date Created", "Name", "Track Count") in History page
- Dashboard "Sync Library" button now navigates to the Library page instead of only refreshing stats
- Disable "Regenerate Missing Tags" button in Doctor page when there are no untagged tracks
- Replace hardcoded light-mode colors with `rx.color_mode_cond` and Radix tokens for dark mode support in History and Tagging pages
- Add proper `on_open_change` handlers to History delete dialog and Tagging tag-all dialog for correct state cleanup

### Fixed
- Fix potential division-by-zero in Doctor page progress bars when `fix_total` is 0
- Remove dead CSS classes (`.nav-link`, `.table-*`, `.btn-*`, `.card-hover`, `.input-focus`) and fix `--navbar-width` variable to match actual 240px value

## [0.5.0] - 2026-03-10

### Added
- Docker support with Dockerfile, docker-compose.yml, and .dockerignore
- GitHub Actions workflow for building & pushing multi-platform Docker images to GHCR on tag push
- Configurable data directory via `PLEXMIX_DATA_DIR` env var for container deployments
- Environment variable fallback for all credentials (keyring-free operation in containers)
- `PLEXMIX_UI_HOST` env var for configuring UI host binding
- Audio feature analysis module powered by Essentia (optional `audio` extra)
- `audio_features` database table with migration for existing databases
- `plexmix audio analyze` and `plexmix audio info` CLI commands
- `--audio` flag on sync commands to extract audio features during sync
- Audio feature enrichment in embedding text (tempo, key, energy, danceability)
- Audio feature filters in playlist generator (tempo, key, energy, danceability)
- Audio filter options in `plexmix create` CLI command
- Audio settings (`AudioSettings`) in config with `AUDIO_` env prefix
- Audio path remapping for Docker/remote setups (`AUDIO_PATH_PREFIX_FROM` / `AUDIO_PATH_PREFIX_TO`)
- File path extraction from Plex media for audio analysis
- Support `GEMINI_API_KEY` env var alongside existing `GOOGLE_API_KEY` for Gemini provider
- Comprehensive test suite for config, recovery, vector index, CLI, and expanded UI states

### Changed
- Migrate `google-generativeai` (EOL) to `google-genai` ^1.0.0 (new client-based SDK)
- Bump `anthropic` from ^0.8.0 to ^0.84.0
- Bump `openai` from ^1.6.0 to ^1.60.0
- Add missing `cohere` ^5.20.0 dependency to pyproject.toml

## [0.4.0] - 2025-12-28

### Changed
- Upgrade Reflex UI runtime from 0.6.8.post1 to 0.8.24 (Vite/Rolldown toolchain)
- Bump Click dependency from 8.1.7 to ^8.2 (required by Reflex 0.8)
- Bump Typer dependency from 0.12.0 to ^0.21.0 (Click 8.3 compatibility)
- Refactor TaggingState cancel mechanism to use module-level dict pattern (Reflex 0.8 compatibility)
- Replace `--reload` CLI flag with `--prod` flag (hot-reloading now default in dev mode)
- Center logo in navbar

### Fixed
- Backend host must be IP address (127.0.0.1) not hostname for Granian compatibility

## [0.3.1] - 2025-12-27

### Added
- Loading interstitial overlay during page navigation for improved UX
- Page loading state management in AppState with automatic reset on page load

### Changed
- Navbar links now trigger loading overlay when clicked
- All page `on_load` handlers now clear loading state when content is ready

## [0.3.0] - 2025-12-27

### Added
- Active-route highlighting in navbar with visual indicator for current page
- Icons for all navbar links using Lucide icons
- CSS design system with design tokens (colors, spacing, radius, shadows, transitions)
- Focus ring styles for improved keyboard accessibility
- Responsive grid layouts for Dashboard, Generator, and Doctor pages
- Real provider tests for AI and embedding settings (replaces stub implementations)
- Global state cleanup handlers for background tasks on client disconnect
- Additional unit tests for TaggingState and LibraryState handlers

### Changed
- Consolidated app entrypoints to use consistent orange theme and dark mode
- Dashboard `on_load` now wired at app level instead of component level
- Doctor `on_load` now wired at app level instead of component level
- Navbar links now have hover/focus transitions and consistent styling
- Navbar width increased with added padding for better visual spacing
- Library stats now use SQLiteManager for consistency with Doctor page
- Generator page layout stacks on mobile screens

### Fixed
- Missing `set_edit_tags`, `set_edit_environments`, `set_edit_instruments` handlers in TaggingState
- Operator precedence bug in tagging page button disabled logic
- Library regenerate confirm dialog Cancel button now properly calls cancel handler
- Track table checkbox callbacks now accept optional checked value parameter

## [0.2.11] - 2025-12-27

### Added
- Bulk query methods for efficient data fetching (`get_tracks_by_ids`, `get_artists_by_ids`, `get_albums_by_ids`, `get_track_details_by_ids`)
- Uniform `complete()` interface across all AI providers with consistent timeout/retry logic
- Exponential backoff retry logic for API calls (3 retries with 1s, 2s, 4s delays)
- Incremental FAISS index updates instead of full rebuilds
- Comprehensive regression tests for database integrity, sync correctness, and provider compatibility
- 'Unknown Artist' and 'Unknown Album' fallback entities for orphaned items during sync

### Changed
- SQLite connection now enables foreign keys, WAL journal mode, and busy timeout
- Insert methods use proper UPSERT pattern to maintain stable row IDs
- Album→artist mapping now uses Plex API's `parentRatingKey` instead of path parsing
- Track update detection now includes rating, play_count, and last_played fields
- Tag preservation during sync updates using COALESCE pattern
- TagGenerator refactored to use uniform provider `complete()` interface
- Playlist generation and sync operations use bulk queries to eliminate N+1 patterns
- Replaced print statements with proper logging in GeminiProvider

### Fixed
- Sync status mismatch: `get_last_sync_time()` now correctly queries `status='success'`
- Albums no longer incorrectly fall back to `artist_id=1` due to rsplit parsing
- Existing tags preserved when syncing track metadata updates
- CLI `create` command no longer requires AI provider API keys for playlist generation
- Cohere provider now passes timeout via `request_options` for proper request timeouts
- Claude provider now passes timeout via `with_options()` for proper request timeouts
- FTS search now uses `bm25()` function for proper relevance ranking instead of `rank`
- CLI `create` command database lock error when saving playlist to Plex
- UI playlist generation stalling due to blocking model load on main thread
- Plex playlist creation failing due to string plex_key not being converted to integer

## [0.2.10] - 2024-12-27

### Fixed
- Settings page local model dropdown fixes and ordering improvements

## [0.2.9] - 2024-12-27

### Added
- Advanced local embedding model support with multiple model options:
  - `all-MiniLM-L6-v2` (384 dimensions, default)
  - `mixedbread-ai/mxbai-embed-large-v1` (1024 dimensions)
  - `google/embeddinggemma-300m` (768 dimensions)
  - `nomic-ai/nomic-embed-text-v1.5` (768 dimensions)

## [0.2.8] - 2024-12-27

### Added
- Multi-provider support for AI and embeddings
- Database health monitoring via Doctor page
- Provider comparison matrices in documentation

## [0.2.7] - 2024-12-27

### Fixed
- Improved Plex connection error handling and diagnostics

### Added
- `LLM.md` with release publishing instructions

## [0.2.6] - 2024-12-26

### Added
- Database reset and recovery system (`DatabaseRecovery`)
- Incremental and regenerate sync modes
- AI tagging step during sync (runs before embeddings)

### Fixed
- KeyboardInterrupt handling during config initialization
- Multi-client state isolation in `LibraryState`

## [0.2.1] - 2024-12-25

### Fixed
- Test suite compatibility with `PlaylistGenerator` changes

## [0.2.0] - 2024-12-25

### Added
- Complete Reflex Web UI implementation
  - Dashboard, Settings, Library, Generator, History, Tagging, Doctor pages
  - Dark/light theme toggle with logo switching
  - Configurable candidate pool multiplier
- Comprehensive web interface for all CLI functionality

## [0.1.4] - 2024-12-24

### Fixed
- Cohere tests using `sys.modules` mocking pattern

## [0.1.3] - 2024-12-24

### Added
- Comprehensive Cohere provider tests
- AI and embedding provider comparison matrices in documentation

## [0.1.2] - 2024-12-23

### Added
- Cohere AI provider integration
- Cohere embedding provider support

## [0.1.1] - 2024-12-22

### Added
- Initial release with core functionality
- Plex library sync with SQLite storage
- FAISS vector search for similarity matching
- AI-powered playlist generation (Gemini, OpenAI, Claude)
- CLI interface with Rich formatting
- Tag generation and management

[Unreleased]: https://github.com/izzoa/plexmix/compare/v0.13.0...HEAD
[0.13.0]: https://github.com/izzoa/plexmix/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/izzoa/plexmix/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/izzoa/plexmix/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/izzoa/plexmix/compare/v0.9.0...v0.10.0
[0.7.0]: https://github.com/izzoa/plexmix/compare/v0.6.7...v0.7.0
[0.6.7]: https://github.com/izzoa/plexmix/compare/v0.6.6...v0.6.7
[0.6.6]: https://github.com/izzoa/plexmix/compare/v0.6.5...v0.6.6
[0.6.5]: https://github.com/izzoa/plexmix/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/izzoa/plexmix/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/izzoa/plexmix/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/izzoa/plexmix/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/izzoa/plexmix/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/izzoa/plexmix/compare/v0.5.8...v0.6.0
[0.5.8]: https://github.com/izzoa/plexmix/compare/v0.5.7...v0.5.8
[0.5.7]: https://github.com/izzoa/plexmix/compare/v0.5.6...v0.5.7
[0.5.6]: https://github.com/izzoa/plexmix/compare/v0.5.5...v0.5.6
[0.5.5]: https://github.com/izzoa/plexmix/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/izzoa/plexmix/compare/v0.5.3...v0.5.4
[0.4.0]: https://github.com/izzoa/plexmix/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/izzoa/plexmix/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/izzoa/plexmix/compare/v0.2.11...v0.3.0
[0.2.11]: https://github.com/izzoa/plexmix/compare/v0.2.10...v0.2.11
[0.2.10]: https://github.com/izzoa/plexmix/compare/v0.2.9...v0.2.10
[0.2.9]: https://github.com/izzoa/plexmix/compare/v0.2.8...v0.2.9
[0.2.8]: https://github.com/izzoa/plexmix/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/izzoa/plexmix/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/izzoa/plexmix/compare/v0.2.1...v0.2.6
[0.2.1]: https://github.com/izzoa/plexmix/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/izzoa/plexmix/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/izzoa/plexmix/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/izzoa/plexmix/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/izzoa/plexmix/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/izzoa/plexmix/releases/tag/v0.1.1
