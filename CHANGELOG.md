# Changelog

All notable changes to PlexMix will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/izzoa/plexmix/compare/v0.7.0...HEAD
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
