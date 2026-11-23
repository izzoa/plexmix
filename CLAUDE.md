### Purpose

This file is the **project-specific playbook** for using Claude (or any coding agent) effectively in the PlexMix repo. It captures the actual conventions in this codebase (CLI + Reflex UI) and a set of best-practice guardrails so changes stay consistent, testable, and safe.

---

### Tech stack (what you’re looking at)

- **Language**: Python **3.10+**
- **Packaging**: **Poetry** (`pyproject.toml`)
- **CLI**: **Typer** + **Rich** (`src/plexmix/cli/main.py`)
- **Web UI (“frontend”)**: **Reflex** (Python UI)
  - Reflex entrypoint lives in `plexmix_ui/plexmix_ui.py`
  - Shared UI code (pages/states/components) lives in `src/plexmix/ui/`
- **Core (“backend”)**:
  - Plex integration via **PlexAPI** (`src/plexmix/plex/`)
  - Local store: **SQLite** (`src/plexmix/database/sqlite_manager.py`)
  - Vector search: **FAISS** (`src/plexmix/database/vector_index.py`)
  - Embeddings: Gemini/OpenAI/Cohere/Custom (OpenAI-compatible)/Local (`src/plexmix/utils/embeddings.py`)
  - AI providers: Gemini/OpenAI/Claude/Cohere/Custom (OpenAI-compatible)/Local LLM (`src/plexmix/ai/`)

---

### Repository layout (high-signal map)

- `src/plexmix/cli/main.py`: Typer CLI (commands: `config`, `sync`, `tags`, `embeddings`, `create`, `ui`, `doctor`, `db`, `audio`, `musicbrainz`)
- `src/plexmix/config/settings.py`: Pydantic settings + YAML config load/save
- `src/plexmix/config/credentials.py`: Credential storage (env var fallback → keyring)
- `src/plexmix/database/`: SQLite manager, models, recovery, FAISS vector index, `index_rebuild.py` (the one function that rebuilds the index from the stored embeddings), backups
- `src/plexmix/plex/`: Plex client + sync engine
- `src/plexmix/ai/`: AI provider implementations + provider factory
- `src/plexmix/playlist/`: Playlist generation logic (`generator.py`) — supports audio feature filters
- `src/plexmix/audio/`: Audio feature analysis via Essentia (`analyzer.py`)
- `src/plexmix/musicbrainz/`: MusicBrainz API client (`client.py`) — genre enrichment, artist MBID resolution
- `src/plexmix/services/musicbrainz_service.py`: MusicBrainz enrichment orchestration (cache-first, artist batching)
- `src/plexmix/ui/`: Reflex pages/states/components (the UI implementation)
  - **Pages**: `index`, `dashboard`, `settings`, `library`, `generator`, `history`, `tagging`, `doctor`
  - **States**: `app_state` (base), `dashboard_state`, `settings_state`, `library_state`, `generator_state`, `history_state`, `tagging_state`, `doctor_state`
  - **Components**: `navbar`, `loading`, `error`, `toast`, `progress_modal`, `track_table`
  - **Utils**: `validation.py` (form validation helpers)
- `plexmix_ui/`: Reflex "app directory" referenced by `rxconfig.py`
- `assets/styles.css`: UI stylesheet overrides (accent colors, etc.)
- `CHANGELOG.md`: Version history (update after each major feature/fix)

Generated/artefacts (generally do **not** edit):
- `dist/`, `htmlcov/`, `__pycache__/`

---

### Setup & common commands

#### Install (dev)

```bash
poetry install
```

#### Install UI extras (Reflex UI)

```bash
poetry install -E ui
```

#### Run CLI locally

```bash
poetry run plexmix --help
```

#### Run the Reflex UI

```bash
# Development (hot reload)
poetry run plexmix ui --dev

# Production build (what users and the Docker image get)
poetry run plexmix ui
```

Notes:
- `plexmix ui` now serves the **production build by default** (`--prod` is still accepted): it builds the frontend before serving, so startup is slow and code edits need a restart. Use `--dev` for hot reload while developing.
- Default UI frontend is `http://localhost:3000`
- Reflex backend defaults to `http://localhost:8000`

#### Tests / lint / format / types

```bash
poetry run pytest
poetry run black src tests
poetry run ruff src tests
poetry run mypy src
```

---

### Configuration & secrets (must-follow conventions)

- **Config file**: `~/.plexmix/config.yaml` (loaded via `Settings.load_from_file()`).
- **Secrets** (Plex token + API keys): stored in the **system keyring** via `src/plexmix/config/credentials.py`.
- **Environment variable fallback**: some UI code also checks env vars (e.g., `GOOGLE_API_KEY`) if keyring is empty.

Rules:
- **Never log secrets** (tokens/API keys) and never write them into repo files.
- **CLI error reports must not show variable values.** Every `typer.Typer(...)` in `src/plexmix/cli/` passes `pretty_exceptions_show_locals=False`. Without it, Typer prints local variables when a command crashes, and they hold the Plex token, API keys and the UI password. Pass it on any new sub-app too; `tests/test_cli_secret_output.py` checks every app registered under the main one.
- **Settings errors must not repeat a value.** `Settings` (in `model_config`) and every section class (in its inner `Config`) in `src/plexmix/config/settings.py` keep `hide_input_in_errors`, so a validation error names the setting and not the value that was rejected. Give a new section class the same flag; `tests/test_config.py` checks every section.
- **Print or log a server address, or an error text from Plex, only through `plexmix.utils.redaction`**: `mask_url_credentials(address)` for an address and `mask_urls_in_text(text)` for an error text. A Plex URL can hold a user name and password, and PlexAPI repeats the URL in its errors. Inside `PlexClient`, format a caught exception with `self._safe_error_text(e)`, which uses the same masking and also removes the client's own token and the credentials of its own address. Never let a PlexAPI error leave `PlexClient` (no bare `raise`): the CLI's error report prints its text, address included. Log the safe text, and raise `PlexReadInterrupted` (a `PlexFetchError`) `from None`: its message is one fixed sentence with no text from Plex.
- When adding new settings:
  - Update the Pydantic models in `src/plexmix/config/settings.py`
  - Update UI settings plumbing in `src/plexmix/ui/states/settings_state.py` (load/save + validations)
  - Prefer backward-compatible defaults

---

### Data model & storage invariants (easy to break)

#### SQLite + FAISS live in the user home directory

- SQLite DB: `~/.plexmix/plexmix.db`
- FAISS index: `~/.plexmix/embeddings.index`
- FAISS metadata: `~/.plexmix/embeddings.metadata` (pickle)

Notes:
- `SQLiteManager.connect()` will auto-initialize the schema if the DB is missing/empty.
- For explicit “missing/corrupt DB” recovery flows, use `src/plexmix/database/recovery.py` (`DatabaseRecovery`).

#### Embedding dimension must match the provider/model

This repo enforces "dimension consistency":
- `VectorIndex` loads `.metadata` and sets `dimension_mismatch` when the existing index dimension doesn't match the current provider's expected dimension.
- UI and CLI both surface mismatch warnings and ask users to regenerate embeddings.

Dimension mapping by provider:
- **Gemini**: 3072
- **OpenAI**: 1536
- **Cohere**: 1024
- **Custom**: user-specified (default 1536)
- **Local models**:
  - `all-MiniLM-L6-v2`: 384 (default)
  - `mixedbread-ai/mxbai-embed-large-v1`: 1024
  - `google/embeddinggemma-300m`: 768
  - `nomic-ai/nomic-embed-text-v1.5`: 768

Rule for changes:
- If you change embedding model/provider defaults, **also update the dimension mapping** (`Settings.embedding.get_dimension_for_provider`) and ensure UX tells users to regenerate.

#### Tags storage format

- `Track.tags` is stored as a **comma-separated string** (helpers: `Track.get_tags_list()`, `Track.set_tags_list()`).
- UI tagging flows may use comma-joined values without spaces; keep parsing tolerant and avoid assuming exact formatting.

#### Tags, embeddings and the index are replaced in place, never cleared first

A provider makes the tags and the embeddings, and making them again costs time and money. A job that clears them first leaves the library without them when it fails or is stopped. So "regenerate" means: write the new data over the old data, track by track.

- **Never delete or blank tags, embedding rows or index files before their replacement exists.** Write the new row over the old one: `update_track_tags` replaces a track's tags, and `insert_embedding` replaces the row of the same track and model. Do not add an `UPDATE tracks SET tags = NULL`, a `DELETE FROM embeddings` or an `unlink` of an index file at the start of a job. After a failure, a refusal, a cancel request or Ctrl+C, every track must have the tags and the embedding it had, or its new ones. Embedding rows of other models are removed only by `finish_embeddings_pass` (`src/plexmix/plex/sync.py`): after a pass that reached its end, and only for tracks that have a row of the current model. `plexmix db reset`, the removal of tracks and the removal of orphaned embeddings are not regenerations and keep their own rules.
- **Never write an empty tag result over existing tags.** When the provider returns no tags, no environments and no instruments for a track (it left the track out, the reply could not be read, or the request for the batch failed), write nothing for that track. Do not call `update_track_tags`, do not let `tags_generated_at` change, and do not count the track as tagged. Save tags after each batch (`TagGenerator.generate_tags_batch(on_batch=...)`), not once at the end.
  - The same holds for each column. Pass `None` to `update_track_tags` for a list that is empty, never `""`: `None` leaves the column as it is. In a regenerate run, a reply without tags is no result for the track, whatever else it holds.
  - **A tag list from a provider reply goes through `clean_tag_list` (`src/plexmix/ai/tag_generator.py`)** before anything decides or writes. It keeps a text that has content once stripped and a number (as text), and drops everything else (`None`, a bool, a dict, a list, a blank text); a value that is not a list gives `[]`. So `[""]`, `[" "]` and `[None]` are empty lists and can neither blank a column nor stamp a track. `TagGenerator._parse_tag_response` uses it for `tags`, `environments` and `instruments`, and every place that writes a tag column from a result applies it again before its per-column rule, because it can be handed results that did not come through the parser: `SyncEngine` (`save_results`), `tags_cmd.py`, Doctor's retag-all handler and the Tagging page job. A new place that writes tags from a provider reply does the same.
  - A regenerate run stops its tags pass when the provider did not answer three requests in a row (`generate_tags_batch(max_failed_batches=...)` raises `RepeatedBatchFailure`). Only a request that says the provider did not answer counts (`provider_did_not_answer`): a retryable error (timeout, rate limit, 5xx) or any error that is not a built-in `ValueError`, `LookupError`, `TypeError` or `AttributeError`, which is what the provider wrappers raise for a reply they could not use ("Empty response from ..."). Such a reply is an answered request, like one that is not JSON: its tracks keep their tags, the row of failures ends and the pass goes on. A refused connection, a closed connection and a timeout still count.
  - A tags pass that reached its end clears its marker entry when tags were replaced since the marker's start time, in this run or in the run it continues (`RegeneratePass.replaced_earlier`), and the run goes on to the embeddings. `RegenerateResult.no_new_tags` is true only when no tags were replaced since that start, which is a new run in which the provider gave nothing usable for any track: the run then makes no embedding, because every embedding would be made again from the text it was made from, and ends as failed. A continued run whose remaining tracks all get nothing ends with warnings ("N tracks keep their previous tags") and not as a failure that repeats for ever. A library with no tracks is a run with nothing to do (`RegenerateResult.nothing_to_do`): no failure, exit 0, no marker file left. After any sequence of regenerate runs with a provider that answers, a later run must be able to reach the end: leave no state that no command clears.
  - An embeddings pass that reached its end clears its marker entry only when no track is left to do (`_close_embeddings_pass`, which `finish_embeddings_pass` and the engine call). A track the provider returned no vector for keeps the entry open, so that the next run makes only those embeddings, in place of starting over for every track and paying for the whole library again. `RegeneratePass.replaced_earlier` is set for both passes, and the commands' report counts it as work (`RegenerateCount.earlier`, `build_regenerate_report`): a continued run that wrote nothing itself, because the provider gave nothing for the tracks that were left, ends with warnings and exit status 0 (tags alone, embeddings alone and both), and only a new run in which the provider gave nothing for any track fails. A command that closes an embeddings pass itself (`embeddings generate --regenerate`) passes `earlier` too.
- **Every regenerate entry point goes through `src/plexmix/services/regenerate_service.py`.** The entry points are `sync regenerate`, `embeddings generate --regenerate`, `doctor --force`, the Library page's "Regenerate" sync and Doctor's "Rebuild embeddings" fix.
  - `check_providers(...)` runs before anything is changed. It sends one test request to each provider the job needs and raises `RegenerateRefused` when one is not ready. The text of that exception holds no key. Its `.error` attribute is the provider's own exception and is not redacted: never print or log it.
  - Tags and embeddings are replaced by `SyncEngine.regenerate_sync` (with Plex) or `SyncEngine.regenerate_local` (without). Embeddings alone are replaced with `generate_embeddings_for_tracks` and closed with `finish_embeddings_pass`. A provider error that stops a pass is not raised by the engine: read `SyncEngine.regenerate_result` after the run, also after Ctrl+C.
  - The run marker (`<database file name>.regenerate.json` beside the database) lets a CLI command continue an unfinished run. Read and write it only through this module (`preview_run`, `open_run`, `finish_scope`) and `plan_regenerate` in `plex/sync.py`. Take the provider and model of a run from `run_provider(settings, scope)`, for the question and for the run, so that both compare the same values.
  - The web UI actions always process every track and never continue an earlier run: the Library sync passes `continue_run=False`, and Doctor's rebuild uses no marker.
  - After a pass that was stopped part-way, rebuild the index from the rows only through `follow_rows_after_stopped_pass` (in `plex/sync.py`, a thin wrapper over the one function below). After a change of the provider or of the vector size the saved index covers every track and the new rows cover a few: the rebuild is then refused and the saved index stays as it is until a pass reaches its end. The stop is what the command reports; the refusal is only logged. Never call `rebuild_index_from_rows` directly after a stopped pass.
  - Give the engine the provider keys from the settings: `SyncEngine(known_secrets=provider_secrets(settings))` in the CLI, `_sync_secrets(settings)` in the web UI. The engine redacts them in the error text it logs and stores in the sync history. Not every provider object keeps its key where the engine can find it.
  - In the web UI a Regenerate sync marks its task (`REGENERATE_RUN_KEY`). It does not start while the "embedding" task or any Doctor fix runs (`embedding_job_running`), and while it runs the Library's embedding task and Doctor's two embedding fixes refuse to start (`regenerate_sync_running`, both in `library_state.py`). Doctor's other fixes do not look at it, and the embedding task and Doctor's embedding fixes do not look at each other, so do not write code that relies on one writer of embeddings.
- **A CLI regenerate command follows one order: terminal check, question, provider check, run.** The shared steps are in `src/plexmix/cli/console_helpers.py`: `require_terminal_or_yes` (first, before anything is loaded), `confirm_regenerate` (default No; No exits 0 with nothing written and no provider asked), `print_regenerate_refused`, `regenerate_report`, `print_regenerate_failed`, `print_regenerate_interrupted` and `print_regenerate_saved` (after a stop that came once both passes had reached their end, only the index is behind: it names `plexmix embeddings generate`, not the command that would start over). A run that regenerated nothing, or that a provider failure stopped part-way, prints no success line and exits 1 (`RegenerateReport.failed`). `--yes` answers only the regenerate question and never allows a large removal.
- **A CLI command that cannot start the embedding provider names the cause.** `create`, `embeddings generate`, `doctor` and `tags generate` build it with `build_embedding_generator_with_reason` (imported as `_build_embedding_generator_with_reason`) and print a failure with `print_embedding_failure(console, failure)` (`src/plexmix/cli/embedding_failure.py`): the title the web UI uses for the cause, the failure's detail (already free of any key) and one next step, each line through `rich.markup.escape`. "API key required for embedding provider." is printed only for a missing key (or no failure object). `create`, `embeddings generate` and `doctor` then exit with status 1. `tags generate` prints the lines in yellow, adds "Skipping." and returns, and its saved tags stay. `plexmix sync` keeps `build_embedding_generator` and prints nothing. A test patches `<module>._build_embedding_generator_with_reason` and returns `(generator, None)` or `(None, failure)`.
- **An index that fails to load is moved aside, never deleted.** `VectorIndex.load_index` renames the index file and its `.metadata` file by adding `.unreadable-<YYYYMMDD-HHMMSS>`, starts with an empty index, sets `load_failed`, `moved_paths` and `unmoved_paths`, and logs one warning. A file that could not be renamed stays in place, and `save_index` writes nothing over it on that object. Keep `vector_index.py` free of `unlink`. An older-format index that was made for another dimension is not migrated: it is flagged as `dimension_mismatch` and left alone. A CLI command that builds a `VectorIndex` calls `print_unreadable_index(console, vector_index)` right after it; `tests/test_cli_console_helpers.py::test_every_cli_command_that_builds_the_index_reports_a_failed_load` checks every module in `src/plexmix/cli/`. The index that the engine opens itself for a removal is `SyncEngine.opened_saved_index`, and the sync commands print the same line for it. Print "index saved" or a success line only when `index_was_saved(vector_index)` is true.
- **Every rebuild of the search index from the database goes through `rebuild_index` in `src/plexmix/database/index_rebuild.py`.** `rebuild_index(db, vector_index, embedding_model, index_path=None)` returns `IndexRebuild(vectors, left_out, saved)`. An index built from the rows may be saved only when it is complete: every track that holds any embedding row must hold a row of the active model whose `embedding_dim` is the index dimension (orphan rows and tracks without any row do not count). When even one track is in the way, nothing is built and nothing is saved: the saved files stay byte for byte as they are, the index object stays as it was loaded (its `dimension_mismatch` flag included), and `left_out` says how many tracks are in the way. `saved` is true only when the files were written. The fixed sentence (`left_out_sentence(count)`, also in the log) names the count and `plexmix embeddings generate --regenerate`.
  - Never call `build_index` and `save_index` yourself to rebuild from the rows. The wrappers `sync.rebuild_index_from_rows`, `sync.follow_rows_after_stopped_pass`, `sync.finish_embeddings_pass` and `tagging_service.rebuild_vector_index` only call `rebuild_index` (the result is also `vector_index.last_rebuild`), and `sync.index_may_follow_rows` asks `tracks_in_the_way`, a read-only count; the CLI and the web UI call them. The two other writers are the removal path of a sync (`SyncEngine._drop_vectors`) and `SyncEngine._save_index_with`, the `update_vectors` branch of a plain sync's embeddings stage. Both write only to an index that holds vectors and loaded from its files without a mismatch or a failed load (`SyncEngine._index_takes_updates`, and the same test in `_update_index`): an index that holds none (its files were moved aside, are gone or were saved empty) has nothing to drop, and extending or saving it would leave an empty or partial index at the path as if it were the library's, with the success line. Such an index is built from every row through `rebuild_index` instead, also when no track needs an embedding. That stage also uses it when it is stopped by Ctrl+C or by a provider error (`_update_index_after_stop`), so that the index holds the vector of every row that was saved: a rebuild from the rows would be refused after a change of the provider name (a track still holds only the earlier row), and the next plain sync embeds only the tracks without a row of the new model, which would leave the vectors of the first run stale. A regenerate pass never takes this branch. `index_rebuild.py` imports nothing from `plex`, the services, the CLI or the web UI, and `tests/test_index_rebuild.py` checks that no module outside `src/plexmix/database/` calls `build_index` and that `plex/sync.py` holds only those two `save_index` calls.
  - `VectorIndex.build_index` never drops vectors. It raises `ValueError` before it changes anything when the vectors do not all have the index dimension. `save_index` returns `False` when it wrote nothing (no path, or an unreadable file that could not be moved aside).
  - What a caller tells the user when `left_out` is not 0: the CLI prints the sentence in yellow through `print_index_left_as_it_is(console, index_left_out(vector_index))` and no line that says the index was rebuilt or saved (a plain `sync` ends with "finished with warnings" and exit 0, `SyncOutcome.index_left_out`, also when no track needs an embedding: the early return of the embeddings stage goes through `SyncEngine._rebuild_index_from_rows` when the loaded index cannot take updates; plain `doctor` does not call a library healthy while `count_tracks_with_only_other_embeddings` is not 0; a regenerate run records `RegenerateResult.index_left_out` only for a pass that reached its end, and a stopped pass reports its stop). The web UI never shows a success message: the job ends failed with `INDEX_NOT_UPDATED_TITLE` (`refused_rebuild(vector_index)` in `library_state.py`) and points to Doctor's "Rebuild embeddings". `plexmix create` keeps reporting the dimension mismatch after a change of the vector size, because the saved index is untouched. After a change of the provider or the model that keeps the vector size nothing reports a mismatch (the flag is about the size alone): the saved index still holds the earlier provider's vectors, and the failed job's own message is what says that it was not updated.
  - The run marker's embeddings entry holds the vector size (`RunProvider.dimension`), so a change of the size between two runs starts a new run; a marker without a size is another identity.

---

### Core workflow patterns (backend)

#### Audio features

- Audio analysis is in `src/plexmix/audio/analyzer.py` (`EssentiaAnalyzer`)
- Requires `essentia` (optional extra: `poetry install -E audio`)
- Features stored in `audio_features` DB table (1:1 with tracks)
- Only tracks with `file_path` set can be analyzed
- Audio features enrich embedding text (tempo, key, energy, danceability) for better semantic search
- Config: `AudioSettings` in `settings.py` (env prefix `AUDIO_`)

#### MusicBrainz enrichment

- Client: `src/plexmix/musicbrainz/client.py` (`MusicBrainzClient`) — wraps `musicbrainzngs` with rate limiting
- Service: `src/plexmix/services/musicbrainz_service.py` — orchestrates enrichment with cache-first lookups and artist MBID batching
- `musicbrainzngs` is a **required** dependency (always installed), but MusicBrainz **usage** is optional (controlled by `MusicBrainzSettings.enabled`)
- Data stored in tracks table: `musicbrainz_recording_id`, `musicbrainz_genres`, `recording_type`, `musicbrainz_enriched_at`
- Cache table: `musicbrainz_cache` with 90-day TTL
- Config: `MusicBrainzSettings` in `settings.py` (env prefix `MUSICBRAINZ_`)
- CLI: `plexmix musicbrainz enrich|info|clear-cache`; also `plexmix sync --musicbrainz`

#### Sync pipeline (Plex → SQLite → MusicBrainz → tags → audio → embeddings → FAISS)

- Sync logic is centralized in `src/plexmix/plex/sync.py` (`SyncEngine`).
- Sync supports:
  - **progress callbacks** (`progress_callback(progress_float, message)`) for UI/CLI feedback
  - **cancellation** via `threading.Event` (`cancel_event`)
- MusicBrainz enrichment (if enabled via `--musicbrainz` flag or `musicbrainz.enrich_on_sync`) runs after DB sync, before tags.
- Tag generation (if AI provider configured) is designed to run **before** embeddings so embeddings can incorporate tags.
- Audio analysis (if enabled via `--audio` flag or `audio.analyze_on_sync`) runs after tags, before embeddings.

Best practices:
- Keep DB writes **parameterized** and inside the `SQLiteManager` APIs.
- If you add expensive work, add a progress hook and make it cancellable.
- **A sync removes tracks only inside `SyncEngine`, and only when Plex's listing leaves them out.** The removal step (`_remove_tracks_plex_no_longer_lists`) and `remove_unlisted_tracks(confirmed_count, held_keys)` are the only places. Both compare the database with the keys Plex listed (`PlexClient.read_report.listed_track_keys`), never with the tracks that were read. Do not add another place that deletes tracks because they are "not in Plex". A removal of more than 50 tracks and more than 5 percent of the library (`removal_needs_confirmation`) is held in `SyncEngine.outcome.held` unless the caller passes `allow_large_removals=True`, and it is applied only after `backup_library` (`src/plexmix/database/backup.py`) has made a copy. Pass `True` only when the user asked for it (`--allow-removals`). To apply a held removal, pass `outcome.held_keys` with the count: a track that went missing after the user was asked is then kept. Pass `None` when the keys are not known (the count alone is then checked); an empty set, or keys that are not text, are refused. An engine without a vector index object gets `saved_index_path` and `saved_index_dimension`, so that the backup holds the index files; never open the saved index before that backup (a load that fails renames the index files to `.unreadable-<date>-<time>` names, and the backup would then not hold them).
- **A removal is applied whole or not at all, and it is recorded before anything else can stop the run.** The deletes are one transaction (`SyncEngine._delete_rows`). A cancel request, an error or Ctrl+C before the commit undoes all of them, so check `cancel_event` only through that method and never commit between deletes. A confirmed removal writes its history row in the same transaction. Right after the commit the engine records the removal (`outcome.removed`, or `last_removal` for `remove_unlisted_tracks`), and only then updates the vector index (`_drop_vectors`, which does not raise and finishes the update after Ctrl+C). Keep that order. A caller whose run raised reads those values to say what was already saved: `outcome.compared`, `outcome.added`, `outcome.updated`, `outcome.removed` and `outcome.backup_folder` after a sync, `last_removal` after an interrupted `remove_unlisted_tracks`. `remove_unlisted_tracks` returns a result for every failure except Ctrl+C (`REMOVAL_FAILED` when the tracks could not be deleted); a result with a `backup_folder` is shown with that folder, also when nothing was removed.
- **A sync's connection check raises a built-in `ConnectionError` before anything is read or written.** Its text ends with "Nothing was changed." That holds for `regenerate_sync` too: it clears nothing and has no connection check of its own, so the one check in `incremental_sync` runs before any tag or embedding is replaced. A `ConnectionError` that arrives after `outcome.compared` is true comes from a later stage (an embedding provider, for one) and must not be reported as Plex or as a sync that changed nothing (`_stopped_before_any_change` in `sync_cmd.py`).
- **In the web UI a held removal belongs to the library its sync compared.** `LibraryState` remembers the hold with a digest of the database path, the Plex address and the Plex library (`hold_scope`). `confirm_removal` ends the hold without a removal when the settings name another one. The 2-second poll shows the notice only to a caller that passes `access_allowed()`.
- **A failed read never removes anything.** A track that Plex listed but that could not be read is kept as it is. Only an item without a title (`EmptyTitleError`) is "skipped"; any other error of a read counts as a failed read. When Plex stops answering or cannot list the library, `PlexClient` raises `PlexReadInterrupted` (a `PlexFetchError`) and `incremental_sync` writes nothing. Its message is one plain sentence with no text from Plex; the masked error text goes to the log only. Catch `PlexFetchError`, show that sentence, and do not report success. An empty listing, an unknown listing and too many failed reads also remove nothing. The engine accepts only a real `PlexReadReport`, so a fake Plex client in a test must set `read_report`. After a run, report from `SyncEngine.outcome` (`SyncOutcome`), and show a success line only when `outcome.is_partial` is false.
- **Other CLI code calls `run_incremental_sync(...)` (`src/plexmix/cli/sync_cmd.py`), never a Typer command function** (`sync_incremental`, `sync_full`, `sync_regenerate`). A Typer command that is called as a plain function gets a truthy `OptionInfo` placeholder for every option the caller leaves out, which switches options such as audio analysis on. `run_incremental_sync` returns `True` only when the sync printed its success line, so print your own "completed" line only then. `tests/test_cli_commands.py::test_cli_modules_never_call_sync_commands_as_functions` checks the rule.

#### Process roles: the local model runtime and FAISS never share a process

The web server process and the CLI process never load the local model runtime (torch, scikit-learn, scipy, transformers, sentence-transformers). A local-model worker process never loads FAISS.

| Process | Local model runtime | FAISS |
|---|---|---|
| Web server | never | on first use |
| CLI | never | on first use |
| Local-model worker | yes | never |

- **Why**: FAISS's OpenMP copy next to torch's in one process crashes on macOS: a segmentation fault in `libomp` when torch loaded first and a FAISS search runs, an `OMP: Error #15` abort when the search came first. A worker holds two copies of its own (torch's and scikit-learn's) and is fine, so do not describe the cause as "two OpenMP copies".
- **Where the imports may be**: the model runtime only inside `_local_embedding_worker` (`src/plexmix/utils/embeddings.py`) and `_local_llm_worker` (`src/plexmix/ai/local_provider.py`); FAISS only inside `_faiss()` (`src/plexmix/database/vector_index.py`). `tests/test_process_isolation.py` enforces it: an `ast` scan of `src/plexmix/`, `plexmix_ui/` and `tests/` (the outermost enclosing function must be an allowed one, so a nested helper cannot hide an import), and a child interpreter that imports the CLI and checks that neither FAISS nor a model library is loaded. It reads `import` statements only; a string-based import is not caught. It needs no model library, so it runs in CI.
- **How server and CLI code reach a local model**: `local_embedding_deps_available()` says whether the package is installed (it asks `importlib.util.find_spec`); `probe_local_embedding_model()` and `warm_up_local_llm()` test or warm up a model in a short-lived worker with a time limit. Never import the runtime to check it, in `src/` or in `tests/` (a test decides its skips with `importlib.util.find_spec`).
- **`VectorIndex` loads FAISS through `_faiss()`**. Call it above the `try` in `load_index`: a FAISS import failure inside it would be taken for a corrupt index.
- **Thread rules**: never set FAISS threads at import time; never use `KMP_DUPLICATE_LIB_OK`; do not set `OMP_NUM_THREADS` in code. The per-call FAISS thread guard is not used; it was left out of this change because it was never tested together with the lazy FAISS import.

#### AI providers

- Provider interface: `src/plexmix/ai/base.py` (`AIProvider`)
- Factory: `src/plexmix/ai/__init__.py` (`get_ai_provider`)

Default models (may change as new versions release):
- **Gemini**: `gemini-2.5-flash`
- **OpenAI**: `gpt-5-mini`
- **Claude**: `claude-sonnet-4-5-20250929`
- **Cohere**: `command-r7b-12-2024`
- **Custom**: user-specified model name (any OpenAI-compatible endpoint)
- **Local**: see `LOCAL_LLM_MODELS` in `local_provider.py`

Conventions:
- Prefer structured outputs (JSON-only responses) and robust parsing/validation.
- Candidate pool sizing is controlled by the `candidate_pool_multiplier` parameter in playlist generation.
- `TagGenerator` is defensive: retries on JSON issues/rate limits and normalizes output (caps tags/env/instruments counts).

When adding a provider:
- Add provider implementation in `src/plexmix/ai/`
- Register in `get_ai_provider(...)` (factory)
- Update UI model lists (Settings page) as needed
- Add/extend tests in `tests/`

---

### UI (“frontend”) patterns (Reflex)

#### Where the UI “app” lives

- Reflex app directory: `plexmix_ui/` (per `rxconfig.py`)
- Reflex pages/states/components: `src/plexmix/ui/`
- Reflex runtime (`reflex run` / `plexmix ui`) is driven by **`plexmix_ui/plexmix_ui.py`** because `rxconfig.py` sets `app_name="plexmix_ui"`.
- `src/plexmix/ui/app.py` also defines an `rx.App` but may be **legacy/stale**; confirm which entrypoint is being used before updating app-level config/routes.

If you add a new page/route:
- Create page under `src/plexmix/ui/pages/`
- Create/extend state under `src/plexmix/ui/states/`
- Wire it into the Reflex app entrypoint (`plexmix_ui/plexmix_ui.py`) and/or `src/plexmix/ui/app.py` (be aware both exist)

#### State & background work

Patterns used throughout states (e.g., `AppState`, `LibraryState`, `GeneratorState`, `HistoryState`, `TaggingState`, `DoctorState`):
- Use `@rx.event(background=True)` for long-running work
- Update state inside `async with self:` blocks
- For CPU-bound or blocking I/O work, use `run_in_executor(...)` so the event loop stays responsive
- For progress updates, use a callback that schedules async state updates (often via `asyncio.create_task(...)`)

Cancellation conventions:
- Sync: per-session cancel events keyed by `self.router.session.client_token` (see `LibraryState`)
- Tagging: a `threading.Event` stored on state (`TaggingState._cancel_event`)
- Session liveness (used by `JobManager.reap_disconnected` to cancel orphaned per-tab work and drop unsaved Settings secret drafts, swept on page loads and every 30 s) relies on Reflex's internal `event_namespace.token_to_sid` via `src/plexmix/ui/session_liveness.py`; recheck it on every Reflex upgrade (if it breaks, liveness reads as unknown and nothing is reaped).
- `AppState._reset_client_storage` overrides a built-in Reflex method (called on every hydrate) so a reload re-checks sign-in from the boot screen; it carries Reflex's private `__override_base_method__` marker, so recheck it on Reflex upgrades too (the render smoke fails at import if the marker stops being honoured).
- The Generator's lost-connection notice (`_thinking_stage` in `src/plexmix/ui/pages/generator.py`) reads Reflex's client-side connection state: `connect_error_var_data` and `has_too_many_connection_errors` from `reflex.components.core.banner` (a Reflex-internal module, not exported at `rx.*`), which compile to the browser's `connectErrors` list. The client adds one entry per failed reconnect attempt (capped at 10), starts the next attempt 200 ms × the number of entries later, and empties the list on connect; the thresholds (`_RECONNECT_AFTER`, `_ALERT_AFTER`) and the timings in the code comments rest on that retry policy. Recheck all of it on every Reflex upgrade. The render smoke pins the compiled expressions and the event-loop hook (`TestLostConnection` in `tests/ui_render/test_generator_render.py`), but CI skips it: after an upgrade, run `pytest tests/ui_render` by hand, as its own session.
- `GeneratorState.in_flight_prompt` (`rx.SessionStorage`, entry `pm_gen_in_flight`) is the browser's copy of the prompt of a generation in flight; `_resume_interrupted_prompt` (called from `on_load`) uses it to say "That playlist didn't finish" and refill an empty prompt after a restart wiped the session. It relies on Reflex sending the browser's storage values (`update_vars_internal`) after the hydrate and before the page's `on_load`, on the browser's value replacing the server's at every hydrate (`_reset_client_storage`), and on `reflex run` wiping `.states` at start. Store nothing but the prompt text in it and never log it. Recheck all of it on every Reflex upgrade: `TestInFlightStorage` in `tests/ui_render/test_generator_render.py` pins the entry name, the event order and the path from the browser's value to the var, but CI skips it, so run `pytest tests/ui_render` by hand, as its own session.

Best practices:
- Don’t do heavy work inside UI render functions; keep logic in state methods.
- Avoid importing heavyweight libraries at module import time in UI states; import inside handlers when possible. This does not apply to the local model runtime (torch, scikit-learn, transformers, sentence-transformers): the server must not import it at all. Use `local_embedding_deps_available()`, `probe_local_embedding_model()` or `warm_up_local_llm()` (see "Process roles" above).

---

### Code style & quality gates (match repo config)

- **Formatting**: Black, line length **100**
- **Lint**: Ruff, line length **100**
- **Typing**: Mypy configured with `disallow_untyped_defs = true`
  - New/changed functions should have type hints (and pass mypy)

Testing:
- Use pytest and fast local fixtures (temp SQLite DBs, mocks)
- UI tests mock Reflex in `tests/ui/conftest.py`
- Avoid network calls in unit tests; mock Plex/LLM providers
- A test that reads `--help` output compares `click.unstyle(result.output)`. GitHub Actions makes Typer colour its help text, and the colour codes split option names. Check a new CLI test file once the way GitHub runs it: `GITHUB_ACTIONS=true CI=true poetry run pytest <paths>` with `NO_COLOR` and `TERM` unset.

---

### CI/CD pipelines

GitHub Actions workflows live in `.github/workflows/` (test, publish, docker). For details on each workflow and the Docker image build process, see the **CI/CD Pipelines** section in `LLM.md`.

---

### Versioning & releases

This repo keeps the version in **two places**:
- `pyproject.toml`
- `src/plexmix/__init__.py`

For detailed release steps and commit message conventions, see `LLM.md`.

#### Changelog maintenance (REQUIRED)

**After completing any major feature, improvement, or bug fix**, you MUST update `CHANGELOG.md`:

1. Add entries under the `[Unreleased]` section at the top
2. Use the appropriate category:
   - `### Added` — new features
   - `### Changed` — changes to existing functionality
   - `### Deprecated` — features that will be removed
   - `### Removed` — removed features
   - `### Fixed` — bug fixes
   - `### Security` — security-related changes
3. Write entries in imperative mood (e.g., "Add dark mode toggle" not "Added dark mode toggle")
4. Be concise but descriptive — include what changed and why it matters to users

When bumping the version for a release:
1. Move all `[Unreleased]` entries to a new version section: `## [X.Y.Z] - YYYY-MM-DD`
2. Add the comparison link at the bottom of the file
3. Update the `[Unreleased]` comparison link to point from the new version to HEAD

Example workflow:
```markdown
## [Unreleased]

### Added
- Multi-provider support for AI embeddings

### Fixed
- Connection timeout on slow networks
```

---

### “How to change things safely” (quick recipes)

#### Keep README.md up to date (REQUIRED)

After any user-facing feature change (new settings, CLI options, env vars, Docker config, etc.), **update `README.md`** to reflect the change. This includes adding new environment variables to the Docker table, documenting new CLI flags, and updating configuration examples.

#### Add a new CLI command

- Implement in `src/plexmix/cli/main.py` under the right Typer app group
- Use Rich tables/progress where applicable
- Add tests under `tests/`
- Update `README.md` if it’s user-facing

#### Add a new DB field

- Update schema in `SQLiteManager.create_tables()`
- Add a lightweight migration in `SQLiteManager._run_migrations()`
- Update Pydantic model(s) in `src/plexmix/database/models.py`
- Add/adjust tests in `tests/test_database.py`

#### Add a new UI feature

- Add/extend a `rx.State` in `src/plexmix/ui/states/`
- Keep long tasks in `@rx.event(background=True)` handlers
- Update the page in `src/plexmix/ui/pages/` and any shared components in `src/plexmix/ui/components/`
- If you add a new route, register it in `plexmix_ui/plexmix_ui.py`
- A new or changed dialog must fit 320 CSS px and 200% browser zoom. Put sizing rules on a class of your own. The only rule allowed on a bare Radix dialog class is `.rt-BaseDialogScrollPadding { min-width: 0 }`, and `tests/ui/test_reflow_fit.py` enforces it. `check_reflow.py` opens only the ⌘K palette, the History detail, the Changelog and, with `--confirm-dialogs`, the History delete confirmation. Check any other dialog by hand at 320×640 and at 640×400. Run the gate with `--confirm-dialogs` only against a throwaway `PLEXMIX_DATA_DIR`.
- Clamped text (`-webkit-line-clamp` needs `overflow: hidden`) is clipped at its box, and ink leaves a line's own box in two places, so give it room there and cancel the room with an equal negative margin (no box moves). Above: Radix pulls the first line of a trimmed heading (`rx.dialog.title`, `rt-r-lt-start`) above its box, which cut the tops off its letters; the playlist dialog title has `padding-top: .5em` and `margin-top: -.5em`. Beside: a box that is only as wide as its text (a heading in a flex row) clips on the advance box of its first and last glyph, which cut the ink of Ï, j or a final bracket; the title and the prompt have `padding-inline: .5em` and `margin-inline: -.5em`. Never a bottom padding, which would show a clipped third line. `check_reflow.py`'s `header-lines` floor (the top only) and `tests/ui/test_reflow_fit.py` check it; a test that looks at glyph ink must pass `fonts=True` to `_layout_page`, because the default harness refuses the app's fonts and the fallback face has other overhangs.

#### Add or update screenshots

Screenshots for the README use composite "hero" images (browser chrome + gradient background). The process:

1. **Capture raw screenshots** using `take_screenshots.py` (Playwright). This saves raw PNGs to `docs/screenshots/` (e.g., `dashboard-dark.png`, `generator.png`).
   - Light mode screenshots use `localStorage.setItem('theme', 'light')` via `addInitScript` + page reload (Reflex uses localStorage, not CSS `prefers-color-scheme`).
   - Run with: `python take_screenshots.py` (requires the Reflex UI running on `localhost:3000`).
2. **Generate hero composites** using `make_hero_screenshots.py` (Pillow). This wraps each raw screenshot in a macOS-style browser frame (traffic lights + URL bar) on a gradient background, outputting `docs/screenshots/hero-*.png`.
   - Run with: `python make_hero_screenshots.py`
   - To add a new screenshot: add its name and gradient config to the `GRADIENTS` dict in the script.
3. **Reference hero images in README.md** using plain `<img>` tags (no inline CSS styles — GitHub strips them).

**Important**: GitHub markdown strips all `style` attributes. Never use inline CSS for screenshot presentation — always bake visual effects into the composite PNG images.

---

### Guardrails for agents

- Don’t commit or generate artefacts: `dist/`, `htmlcov/`, `__pycache__/`.
- Don’t print/log secrets.
- Prefer `logging` over `print()` in library code. (CLI user output should go through Rich `Console`.)
- Prefer small, focused diffs; when refactoring, keep behavior stable and add tests.
- A scratch profile (a temporary `PLEXMIX_DATA_DIR` for a test or a manual run) must contain its own `config.yaml`; an empty file is enough. When that folder has none, `Settings.load_from_file()` falls back to the real `~/.plexmix/config.yaml` in the home directory, so the run reads the user's real settings.
