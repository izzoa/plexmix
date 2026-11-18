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
- `src/plexmix/database/`: SQLite manager, models, recovery, FAISS vector index
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
- **A sync removes tracks only inside `SyncEngine`, and only when Plex's listing leaves them out.** The removal step (`_remove_tracks_plex_no_longer_lists`) and `remove_unlisted_tracks(confirmed_count, held_keys)` are the only places. Both compare the database with the keys Plex listed (`PlexClient.read_report.listed_track_keys`), never with the tracks that were read. Do not add another place that deletes tracks because they are "not in Plex". A removal of more than 50 tracks and more than 5 percent of the library (`removal_needs_confirmation`) is held in `SyncEngine.outcome.held` unless the caller passes `allow_large_removals=True`, and it is applied only after `backup_library` (`src/plexmix/database/backup.py`) has made a copy. Pass `True` only when the user asked for it (`--allow-removals`). To apply a held removal, pass `outcome.held_keys` with the count: a track that went missing after the user was asked is then kept. Pass `None` when the keys are not known (the count alone is then checked); an empty set, or keys that are not text, are refused. An engine without a vector index object gets `saved_index_path` and `saved_index_dimension`, so that the backup holds the index files; never open the saved index before that backup (a load that fails destroys the file).
- **A removal is applied whole or not at all, and it is recorded before anything else can stop the run.** The deletes are one transaction (`SyncEngine._delete_rows`). A cancel request, an error or Ctrl+C before the commit undoes all of them, so check `cancel_event` only through that method and never commit between deletes. A confirmed removal writes its history row in the same transaction. Right after the commit the engine records the removal (`outcome.removed`, or `last_removal` for `remove_unlisted_tracks`), and only then updates the vector index (`_drop_vectors`, which does not raise and finishes the update after Ctrl+C). Keep that order. A caller whose run raised reads those values to say what was already saved: `outcome.compared`, `outcome.added`, `outcome.updated`, `outcome.removed` and `outcome.backup_folder` after a sync, `last_removal` after an interrupted `remove_unlisted_tracks`. `remove_unlisted_tracks` returns a result for every failure except Ctrl+C (`REMOVAL_FAILED` when the tracks could not be deleted); a result with a `backup_folder` is shown with that folder, also when nothing was removed.
- **A sync's connection check raises a built-in `ConnectionError` before anything is read or written.** Its text ends with "Nothing was changed." (or with "The library was not synced." for the check that `regenerate_sync` runs after it has cleared the tags and embeddings). A `ConnectionError` that arrives after `outcome.compared` is true comes from a later stage (an embedding provider, for one) and must not be reported as Plex or as a sync that changed nothing (`_stopped_before_any_change` in `sync_cmd.py`).
- **In the web UI a held removal belongs to the library its sync compared.** `LibraryState` remembers the hold with a digest of the database path, the Plex address and the Plex library (`hold_scope`). `confirm_removal` ends the hold without a removal when the settings name another one. The 2-second poll shows the notice only to a caller that passes `access_allowed()`.
- **A failed read never removes anything.** A track that Plex listed but that could not be read is kept as it is. Only an item without a title (`EmptyTitleError`) is "skipped"; any other error of a read counts as a failed read. When Plex stops answering or cannot list the library, `PlexClient` raises `PlexReadInterrupted` (a `PlexFetchError`) and `incremental_sync` writes nothing. Its message is one plain sentence with no text from Plex; the masked error text goes to the log only. Catch `PlexFetchError`, show that sentence, and do not report success. An empty listing, an unknown listing and too many failed reads also remove nothing. The engine accepts only a real `PlexReadReport`, so a fake Plex client in a test must set `read_report`. After a run, report from `SyncEngine.outcome` (`SyncOutcome`), and show a success line only when `outcome.is_partial` is false.
- **Other CLI code calls `run_incremental_sync(...)` (`src/plexmix/cli/sync_cmd.py`), never a Typer command function** (`sync_incremental`, `sync_full`, `sync_regenerate`). A Typer command that is called as a plain function gets a truthy `OptionInfo` placeholder for every option the caller leaves out, which switches options such as audio analysis on. `run_incremental_sync` returns `True` only when the sync printed its success line, so print your own "completed" line only then. `tests/test_cli_commands.py::test_cli_modules_never_call_sync_commands_as_functions` checks the rule.

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

Best practices:
- Don’t do heavy work inside UI render functions; keep logic in state methods.
- Avoid importing heavyweight libraries at module import time in UI states; import inside handlers when possible.

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
