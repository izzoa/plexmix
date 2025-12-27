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
  - Embeddings: Gemini/OpenAI/Cohere/Local (`src/plexmix/utils/embeddings.py`)
  - AI providers: Gemini/OpenAI/Claude/Cohere/Local LLM (`src/plexmix/ai/`)

---

### Repository layout (high-signal map)

- `src/plexmix/cli/main.py`: Typer CLI (commands: `config`, `sync`, `tags`, `embeddings`, `create`, `ui`, `doctor`, `db`)
- `src/plexmix/config/settings.py`: Pydantic settings + YAML config load/save
- `src/plexmix/config/credentials.py`: Keyring credential storage (token/API keys)
- `src/plexmix/database/`: SQLite manager, models, recovery, FAISS vector index
- `src/plexmix/plex/`: Plex client + sync engine
- `src/plexmix/ai/`: AI provider implementations + provider factory
- `src/plexmix/playlist/`: Playlist generation logic (`generator.py`)
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
poetry run plexmix ui
```

Notes:
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

#### Sync pipeline (Plex → SQLite → tags → embeddings → FAISS)

- Sync logic is centralized in `src/plexmix/plex/sync.py` (`SyncEngine`).
- Sync supports:
  - **progress callbacks** (`progress_callback(progress_float, message)`) for UI/CLI feedback
  - **cancellation** via `threading.Event` (`cancel_event`)
- Tag generation (if AI provider configured) is designed to run **before** embeddings so embeddings can incorporate tags.

Best practices:
- Keep DB writes **parameterized** and inside the `SQLiteManager` APIs.
- If you add expensive work, add a progress hook and make it cancellable.

#### AI providers

- Provider interface: `src/plexmix/ai/base.py` (`AIProvider`)
- Factory: `src/plexmix/ai/__init__.py` (`get_ai_provider`)

Default models (may change as new versions release):
- **Gemini**: `gemini-2.5-flash`
- **OpenAI**: `gpt-5-mini`
- **Claude**: `claude-sonnet-4-5-20250929`
- **Cohere**: `command-r7b-12-2024`
- **Local**: see `LOCAL_LLM_MODELS` in `local_provider.py`

Conventions:
- Prefer structured outputs (JSON-only responses) and robust parsing/validation.
- Candidate truncation is enforced based on model context limits (`AIProvider.get_max_candidates()`).
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

---

### Guardrails for agents

- Don’t commit or generate artefacts: `dist/`, `htmlcov/`, `__pycache__/`.
- Don’t print/log secrets.
- Prefer `logging` over `print()` in library code. (CLI user output should go through Rich `Console`.)
- Prefer small, focused diffs; when refactoring, keep behavior stable and add tests.
