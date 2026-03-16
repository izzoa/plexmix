# PlexMix — Consolidated Improvement Plan

> Unified from Claude, Codex, and Kimi analyses (2026-03-14, v0.6.7)

---

## Phase 1 — Stabilize Build & Fix Known Issues ✅

**Effort:** ~3 hours | **Risk:** Low | **Blocks:** Everything else

The first pass is not new features — it's making the existing codebase safe to iterate on.

### CLI startup safety
- [x] Make file logging best-effort: catch `PermissionError` in `src/plexmix/utils/logging.py` so `plexmix --help` works without `~/.plexmix/` being writable
- [x] Add regression tests for CLI help paths in restricted environments

### Correctness fixes
- [x] Fix undefined `embedding_provider` reference on the playlist dimension-mismatch path in `src/plexmix/cli/main.py`
- [x] Clean current Ruff failures in application code (`poetry run ruff check src tests`)
- [x] Reduce Mypy failures in non-UI core modules (`poetry run mypy src`) — 171 errors → 0

---

## Phase 2 — Reflex 0.8 Upgrade ✅

**Effort:** ~2 hours | **Risk:** Medium | **Blocks:** Phase 6 (UI revamp)

All items were already completed prior to this improvement pass.

### Dependency updates
- [x] Bump `click` from `==8.1.7` to `^8.2` in `pyproject.toml`
- [x] Bump `reflex` to `^0.8.23` in `pyproject.toml` and `requirements.txt` (running 0.8.24)

### Code migration
- [x] Refactor `TaggingState._cancel_event` from State field to module-level dict (match `LibraryState` pattern)
- [x] Add cleanup handler for module-level cancel events (`_cleanup_tagging_state`, `_cleanup_all_tagging_state`, `atexit.register`)
- [x] Remove `--reload` flag usage (hot-reload is default in 0.8)
- [x] Verify CLI flags (`--frontend-port`, `--backend-host`, `--prod`)
- [x] Verify static asset paths (stylesheets, logos) under Vite/Rolldown

### Validation
- [x] Run full test suite + manual UI smoke test (316 passed, 1 skipped)
- [x] Update CHANGELOG.md

---

## Phase 3 — CI Pipeline Hardening ✅

**Effort:** ~1 hour | **Risk:** Low

- [x] Add Black formatting check to `.github/workflows/test.yml`
- [x] Add Ruff lint check to `.github/workflows/test.yml`
- [x] Add Mypy type check to `.github/workflows/test.yml` (core modules only — excludes UI pages due to Reflex dynamic types)
- [x] Set coverage threshold (fail CI if coverage drops below 35%, raise over time)
- [x] Add regression tests for permission-restricted environments (CLI help, Docker) *(done in Phase 1)*

---

## Phase 4 — Architectural Cleanup

**Effort:** ~2–3 days | **Risk:** Medium | **Highest structural value**

This is the most important structural change. Orchestration logic is duplicated across CLI and Reflex states — both reassemble providers, databases, and workflows independently. Centralizing this makes every future change cheaper and safer.

### 4A — Extract shared service layer
- [x] Create `src/plexmix/services/providers.py` — single provider factory (resolve settings → credentials → provider instance)
- [x] Refactor CLI to use shared provider service (removed ~100 LOC of inline provider construction)
- [x] Refactor UI states (library, tagging, generator, doctor, settings) to use shared provider service (removed ~200 LOC of duplicated API key resolution)
- [x] Create `src/plexmix/services/sync_service.py` — unified sync orchestration (`connect_plex`, `open_db`, `build_vector_index`)
- [x] Create `src/plexmix/services/playlist_service.py` — playlist generation orchestration (`build_generation_filters`, `safe_int`, `safe_float`)
- [x] Create `src/plexmix/services/tagging_service.py` — tag/embedding orchestration (`generate_embeddings_for_tracks`, `rebuild_vector_index`, `build_track_embedding_data`)
- [x] Create `src/plexmix/services/audio_service.py` — audio analysis orchestration (`get_analyzable_tracks`, `analyze_tracks`)
- [x] Refactor CLI commands (sync, create, tags, embeddings, audio) to use service layer
- [x] Refactor UI states (library, generator) to use service layer

### 4B — Consolidate provider metadata into one registry ✅
- [x] Create a single provider registry defining: id, display name, default model, supported models, API key requirement, embedding dimensions, capability flags (AI / embeddings / both) → `src/plexmix/services/registry.py`
- [x] Replace duplicated provider/model lists across `ai/__init__.py`, `config/settings.py`, `ui/states/settings_state.py`, `services/providers.py`
- [x] Use registry in AI factory (`get_ai_provider`), settings UI dropdowns (`update_model_lists`, `_sync_embedding_dimension`), dimension lookup (`get_dimension_for_provider`), and API key checks

### 4C — Consolidate Reflex app entrypoint ✅
- [x] Delete `src/plexmix/ui/app.py` (dead code; `plexmix_ui/plexmix_ui.py` is the active entrypoint)
- [x] Route registration, theme config, and shared app setup already live in `plexmix_ui/plexmix_ui.py`

### 4D — Break up oversized files
- [x] Split `src/plexmix/cli/main.py` (1,579→57 lines) into 9 command modules: `config_cmd.py`, `sync_cmd.py`, `tags_cmd.py`, `embeddings_cmd.py`, `create_cmd.py`, `db_cmd.py`, `audio_cmd.py`, `ui_cmd.py`, `doctor_cmd.py`
- [x] Split `SettingsState` (1,068→638 lines) — extract testing handlers into `_settings_testing.py` (373 lines) and download handlers into `_settings_downloads.py` (119 lines)
- [x] Split `settings.py` page (1,045→364 lines) — extract AI provider and embedding sections plus shared helpers into `_settings_sections.py` (704 lines)
- *Deferred*: `library.py` (658 lines) — borderline size, not worth splitting further

### 4E — Extract shared UI components
- [x] Extract `_stat_tile()` from `dashboard.py` and `doctor.py` into `components/stat_tile.py`
- [x] Deduplicate `_str_dict()` and `_format_eta()` helpers into `ui/utils/helpers.py` (was in 3 state files)
- [x] Extract `form_field()` / `year_range_field()` / `help_text()` into `ui/utils/form_utils.py`; applied to tagging page
- *Deferred*: Filter accordion extraction — pages have different enough structures that a shared component would be over-engineering
- [x] Extract hardcoded magic numbers (batch sizes, pagination, stagger delays) into `config/constants.py`

---

## Phase 5 — Test Coverage Push (35% → 60%+)

**Effort:** ~10–15 hours | **Risk:** Low

Focus should shift from isolated unit coverage toward behavior and workflow coverage.

**Progress: 35% → 47% (639 tests, +323 new)**

### Service layer (new)
- [x] 59 tests for `services/providers.py` covering all 6 functions with edge cases
- [x] 50 tests for `services/registry.py` covering all query helpers and registry data

### Sync engine (8% → 60%)
- [x] Test full sync flow with mocked Plex client + real temp SQLite DB (21 tests)
- [x] Test progress callback invocation and timing
- [x] Test cancellation via `threading.Event`
- [x] Test `regenerate_sync` clears tags/embeddings
- [x] Test error recovery (network timeouts, corrupt metadata) — 29 tests in `test_sync_errors.py`
- [x] Test audio analysis post-sync integration — 7 audio service tests in `test_sync_errors.py`

### CLI commands (partial → 70%)
- [x] Add integration tests using `typer.testing.CliRunner` (16 tests)
- [x] Test `sync`, `tags`, `embeddings`, `create`, `db`, `audio` commands
- [x] Verify Rich output and exit codes
- [x] Test CLI help under restricted environments (done in Phase 1)
- [x] Test `doctor` command (6 tests): healthy DB, missing embeddings, orphaned cleanup, force mode
- [x] Test `config` commands (12 tests): test/show/init with success and failure paths

### Plex client (27% → 70%)
- [x] Test connection logic with retries, Unauthorized, BadRequest handling
- [x] Test metadata extraction with missing/malformed fields (empty name, no genres)
- [x] Test file path extraction from `media.parts[0].file`
- [x] Test `get_music_libraries`, `select_library`, `test_connection`, `create_playlist`
- [x] Test token whitespace cleaning

### UI states (~20% → 60%)
- [ ] Expand `tests/ui/test_states.py` to cover all 8 state classes
- [ ] Test SettingsState validation and provider testing handlers
- [ ] Test GeneratorState playlist generation flow
- [ ] Test LibraryState sync/embed/audio handlers

### Embedding pipeline
- [x] Test `EmbeddingGenerator.generate_embedding()` with all provider types — 6 provider tests + 6 dimension mismatch tests in `test_embedding_pipeline.py`
- [x] Test dimension mismatch detection end-to-end — verify_dimension, full rebuild after mismatch
- [x] Test embedding → vector index → search pipeline — 6 end-to-end pipeline tests + 10 tagging service integration tests

### End-to-end workflows
- [ ] Full pipeline test: sync → tag → audio → embed → generate → save
- [ ] Service-layer tests (once Phase 4A is complete)

---

## Phase 6 — UI Design System & Revamp

**Effort:** 19–28 days | **Risk:** Low (no state changes, visual only)

### Preparation
- [ ] Consolidate `UI_REVAMP_CLAUDE.md`, `UI_REVAMP_CODEX.md`, `UI_REVAMP_FINAL.md`, `UI_REVAMP_KIMI.md` into one canonical plan
- [ ] Delete superseded revamp docs

### Implementation
- [ ] Phase 0: Finalize design token system (much already exists in `assets/styles.css`)
- [ ] Phase 1: Navigation redesign
- [ ] Phase 2: Dashboard page
- [ ] Phase 3: Settings page
- [ ] Phase 4: Library page
- [ ] Phase 5: Generator page (hero page — highest visual impact)
- [ ] Phase 6: History page
- [ ] Phase 7: Tagging page
- [ ] Phase 8: Doctor page
- [ ] Phase 9: Shared component polish
- [ ] Phase 10: Responsive polish (mobile table → card view, ARIA labels, 5-breakpoint QA)

### UX consistency pass (run alongside or after page revamp)
- [ ] Standardize progress UX across sync, tagging, doctor, embeddings, and audio analysis
- [ ] Standardize empty states, warning banners, and error recovery actions
- [ ] Surface dimension mismatch, missing embeddings, and provider misconfiguration uniformly

### Quality gates
- [ ] 100% design token usage (no raw color/spacing values)
- [ ] Lighthouse performance score >90
- [ ] WCAG 2.1 AA compliance
- [ ] >80% component reuse across pages

---

## Phase 7 — Feature Expansion (README Roadmap)

**Effort:** ~8 weeks | **Risk:** Medium

These are the features listed in the README roadmap. The service layer from Phase 4 makes each one significantly easier.

### 7A — Playlist Export/Import (High Priority) ✅
- [x] Backend: `plexmix playlist export <id> --format m3u/json` CLI command
- [x] Backend: `plexmix playlist import <file> --format m3u/json` CLI command
- [x] Backend: `plexmix playlist list` CLI command
- [x] M3U format support (real file paths when available, fallback to artist-title)
- [x] JSON format support (full metadata including plex_key for lossless round-trip)
- [x] UI: JSON export button on History page (card + detail modal)
- [x] Fix: M3U export now uses real file paths and correct `artist_name` field
- [x] Database: `get_track_by_file_path()` and `file_path` added to playlist track queries
- [x] UI: Import modal on History page with file upload (JSON/M3U, auto-detect format, track matching)

### 7B — Smart Shuffle & Ordering (High Priority)
- [x] Add `shuffle_mode` parameter: `similarity`, `random`, `alternating_artists`, `energy_curve`
- [x] Energy curve algorithm (gradually increase/decrease energy through playlist)
- [x] Alternating artists algorithm (maximize artist diversity in sequence)
- [x] UI: Shuffle mode selector on Generator page
- [ ] UI: Preview shuffle before generation

### 7C — Playlist Templates (High Priority) ✅
- [x] Database schema for template storage (`playlist_templates` table with migration)
- [x] Template CRUD API in SQLiteManager (`insert_template`, `get_templates`, `get_template_by_id`, `delete_template`)
- [x] Save current generator config as named template (dialog on Generator page)
- [x] Template library with presets ("Morning Commute", "Workout", "Study Session", "Dinner Party", "Late Night")
- [x] UI: Template gallery on Generator page (horizontally scrollable cards, apply on click)

### 7D — Multi-Library Support (Medium Priority)
- [ ] Database migration to track library per track
- [ ] Multi-library sync support in service layer
- [ ] Cross-library playlist generation option
- [ ] UI: Library selector in settings / navbar

### 7E — Playlist Preview Before Save (High Priority) ✅
- [x] Generator page already shows full track list after generation without auto-saving
- [x] Track table with remove-track and regenerate buttons serves as the preview
- [x] Users explicitly choose "Save to Plex" or "Save Locally" to persist

---

## Phase 8 — Backend Enhancements

**Effort:** Variable | **Risk:** Medium

### Infrastructure
- [x] Replace module-global UI task state (`_sync_cancel_events`, `_audio_cancel_events`, etc.) with a job manager abstraction (job IDs, status, progress, cancellation, timestamps, optional SQLite persistence)
- [x] Incremental FAISS index updates (switch to `IndexIDMap` for add/update/remove without full rebuild)
- [x] Formalize schema migration versioning (replace ad hoc migration checks)

### Provider improvements
- [x] Streaming responses for local LLM provider (reduce perceived latency)
- [x] Provider auto-discovery — detect available models from Ollama endpoint
- [x] Plex token expiry detection and pre-flight validation before sync
- [x] Smarter tag regeneration targeting only stale tracks

### Integrations (lower priority)
- [ ] Webhook endpoint for Plex events (auto-trigger sync on library update)
- [ ] Last.fm integration (scrobble playlist plays, import top tracks for personalization)
- [ ] MusicBrainz integration (canonical IDs, better metadata accuracy)
- [x] Duplicate avoidance across historical playlists

### Nice-to-have
- [ ] Database connection pooling for concurrent access
- [x] Embedding dimension auto-detection (query API to verify actual dimensions)
- [x] Query result pagination for library search
- [ ] Caching layer for frequently accessed data (Redis or in-memory)

---

## Phase 9 — UI Feature Additions

**Effort:** Variable | **Risk:** Low

- [x] Playlist reordering in History detail view (move up/down + save order)
- [x] Bulk operations in Library (multi-select → apply tags, delete with confirmation)
- [x] Keyboard shortcuts for common actions (vim-style `g+key` nav, `/` search focus, `Esc` blur)
- [x] Richer library drill-down by tag and audio feature filters
- [x] Rerun historical playlist generations (full config stored with playlists, "Rerun" button in History detail)
- [x] Saved prompt chips / filter presets in generator (covered by existing template gallery system)
- [x] Better mobile ergonomics for library, generator, and history pages (icon-only buttons, responsive sliders, hidden year range)
- [ ] PWA support (low priority)

---

## Phase 10 — Documentation & Release

- [x] Update README.md to reflect all new features, settings, and CLI flags
- [x] Update CHANGELOG.md with all changes under `[Unreleased]` → moved to `[0.7.0]`
- [x] Cut release with version bump in `pyproject.toml` + `src/plexmix/__init__.py` (0.6.7 → 0.7.0)
- [x] Clean up planning docs (`UPGRADE.md`, `VERSION_BUMP*.md`, `IMPROVEMENTS_*.md`, revamp docs)
- [ ] Consider adding: OpenAPI/Swagger docs, CONTRIBUTING.md with test guidelines, performance tuning guide for large libraries
