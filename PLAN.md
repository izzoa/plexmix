# PlexMix Development Plan
## AI-Powered Plex Playlist Generator - Implementation Roadmap

---

## Project Overview

**PlexMix** is a Python-based CLI application that connects to a user's Plex server, synchronizes their music library to a local SQLite database with FAISS vector index, and uses AI providers (OpenAI GPT, Google Gemini, Anthropic Claude) to generate mood-based playlists.

### Technology Stack
- **Language**: Python 3.10+
- **CLI Framework**: Typer
- **Package Manager**: Poetry
- **Database**: SQLite + FAISS (vector similarity search)
- **Plex Integration**: PlexAPI
- **AI Providers**: OpenAI, Google Generative AI, Anthropic
- **Default AI Provider**: Google Gemini (gemini-2.5-flash)
- **Default Embedding Provider**: Google Gemini (gemini-embedding-001, 3072 dims)
- **Alternative Embedding Providers**: OpenAI text-embedding-3-small (1536 dims), sentence-transformers (local)
- **Additional**: Rich (console), Pydantic (validation), Keyring (credentials), python-dotenv
- **License**: MIT

### Architecture Decision
- **SQLite**: Primary storage for all music metadata (tracks, albums, artists, genres)
- **FAISS**: Separate vector index for semantic/mood-based search
- **Two-stage retrieval**: SQL filters → FAISS similarity → LLM playlist generation

---

## Project Configuration Decisions

The following decisions have been made and are reflected throughout this plan:

1. **CLI Framework**: Typer (modern, type-hint based)
2. **Package Manager**: Poetry
3. **Default Embedding Provider**: Google Gemini gemini-embedding-001 (3072 dimensions)
4. **Alternative Embedding Providers**: OpenAI text-embedding-3-small (1536 dims), sentence-transformers (local, free, offline)
5. **Default AI Provider**: Google Gemini gemini-2.5-flash
6. **Alternative AI Providers**: OpenAI GPT, Anthropic Claude
7. **Embedding Text Format**: `"{title} by {artist} from {album} - {genres} ({year})"`
8. **Database Location**: `~/.plexmix/` (user home directory)
9. **Deleted Track Handling**: Hard delete (remove from database entirely)
10. **Default Playlist Length**: 50 tracks (user prompted for confirmation/change)
11. **Default Candidate Pool Size**: 100 tracks
12. **License**: MIT
13. **Initial Sync Behavior**: Prompt user to run sync or defer after setup wizard
14. **Single API Key Default**: Only Google API key required for default configuration (both AI and embeddings)

---

## Phase 0: Project Setup & Foundation

### TODO 0.1: Initialize Project Structure
- [ ] Create project directory structure:
  ```
  plexmix/
  ├── src/
  │   └── plexmix/
  │       ├── __init__.py
  │       ├── cli/
  │       │   ├── __init__.py
  │       │   └── main.py
  │       ├── plex/
  │       │   ├── __init__.py
  │       │   └── client.py
  │       ├── database/
  │       │   ├── __init__.py
  │       │   ├── sqlite_manager.py
  │       │   └── vector_index.py
  │       ├── ai/
  │       │   ├── __init__.py
  │       │   ├── base.py
  │       │   ├── openai_provider.py
  │       │   ├── gemini_provider.py
  │       │   └── claude_provider.py
  │       ├── playlist/
  │       │   ├── __init__.py
  │       │   └── generator.py
  │       ├── config/
  │       │   ├── __init__.py
  │       │   └── settings.py
  │       └── utils/
  │           ├── __init__.py
  │           ├── logging.py
  │           └── embeddings.py
  ├── tests/
  │   ├── __init__.py
  │   ├── test_plex.py
  │   ├── test_database.py
  │   ├── test_ai.py
  │   └── test_playlist.py
  ├── docs/
  ├── .env.example
  ├── .gitignore
  ├── pyproject.toml
  ├── README.md
  └── PLAN.md (this file)
  ```

### TODO 0.2: Setup Poetry Configuration
- [ ] Initialize Poetry project: `poetry init`
- [ ] Create `pyproject.toml` with Poetry configuration
- [ ] Define project metadata (name: plexmix, version: 0.1.0, description, authors)
- [ ] Set license to MIT
- [ ] Add core dependencies:
  - `plexapi` - Plex server integration
  - `typer` - CLI framework
  - `rich` - Console formatting and progress bars
  - `pydantic` - Data validation and settings
  - `python-dotenv` - Environment variable management
  - `keyring` - Secure credential storage
  - `openai` - OpenAI API client (for embeddings and optional GPT provider)
  - `google-generativeai` - Google Gemini API (default AI provider)
  - `anthropic` - Anthropic Claude API (optional provider)
  - `faiss-cpu` - FAISS vector similarity search
  - `numpy` - Array operations for embeddings
  - `sentence-transformers` - Local embedding models (optional alternative)
  - `pyyaml` - YAML configuration file support
- [ ] Add development dependencies:
  - `pytest` - Testing framework
  - `pytest-cov` - Coverage reporting
  - `black` - Code formatting
  - `ruff` - Linting
  - `mypy` - Type checking

### TODO 0.3: Environment and Configuration Setup
- [ ] Create `.env.example` file with template variables:
  - `PLEX_URL` - Plex server URL
  - `PLEX_TOKEN` - Plex authentication token
  - `GOOGLE_API_KEY` - Google Gemini API key (required for default AI provider and embeddings)
  - `OPENAI_API_KEY` - OpenAI API key (optional, for alternative embeddings/AI)
  - `ANTHROPIC_API_KEY` - Anthropic Claude API key (optional, for alternative AI)
  - `DATABASE_PATH` - Path to SQLite database (default: ~/.plexmix/plexmix.db)
  - `FAISS_INDEX_PATH` - Path to FAISS index file (default: ~/.plexmix/embeddings.index)
  - `DEFAULT_AI_PROVIDER` - Default AI provider (default: gemini)
  - `DEFAULT_EMBEDDING_PROVIDER` - Default embedding provider (default: gemini)
  - `LOG_LEVEL` - Logging level (DEBUG/INFO/WARNING/ERROR)
- [ ] Create `.gitignore` to exclude:
  - `__pycache__/`
  - `*.pyc`
  - `.env`
  - `*.db`
  - `*.index`
  - `.venv/`
  - `dist/`
  - `build/`

### TODO 0.4: Initialize Git Repository
- [ ] Run `git init`
- [ ] Create initial commit with project structure
- [ ] Add `.gitignore` before committing sensitive files

---

## Phase 1: Database Layer - SQLite Schema & Models

### TODO 1.1: Design SQLite Database Schema
- [ ] Create database schema design document outlining:
  - **artists** table: id (PK), plex_key (unique), name, genre, bio
  - **albums** table: id (PK), plex_key (unique), title, artist_id (FK), year, genre, cover_art_url
  - **tracks** table: id (PK), plex_key (unique), title, artist_id (FK), album_id (FK), duration_ms, genre, year, rating, play_count, last_played, file_path
  - **genres** table: id (PK), name (unique)
  - **track_genres** junction table: track_id (FK), genre_id (FK)
  - **embeddings** table: id (PK), track_id (FK), embedding_model, embedding_dim, vector (BLOB), created_at, updated_at
  - **sync_history** table: id (PK), sync_date, tracks_added, tracks_updated, tracks_removed, status, error_message
  - **playlists** table: id (PK), plex_key, name, description, created_by_ai, mood_query, created_at
  - **playlist_tracks** junction table: playlist_id (FK), track_id (FK), position

### TODO 1.2: Create Pydantic Models for Data Validation
- [ ] In `src/plexmix/database/models.py`, create Pydantic models:
  - `Artist` model with validation
  - `Album` model with validation
  - `Track` model with validation and relationships
  - `Genre` model
  - `Embedding` model with vector array validation
  - `SyncHistory` model
  - `Playlist` model
- [ ] Add type hints and field validators
- [ ] Implement serialization/deserialization methods

### TODO 1.3: Implement SQLite Manager
- [ ] In `src/plexmix/database/sqlite_manager.py`, create `SQLiteManager` class:
  - `__init__(db_path)` - Initialize database connection
  - `create_tables()` - Create all tables with proper indexes
  - `get_connection()` - Return database connection
  - `close()` - Close database connection
  - Implement context manager protocol (`__enter__`, `__exit__`)

### TODO 1.4: Create Database Access Layer (DAL)
- [ ] In `src/plexmix/database/sqlite_manager.py`, add CRUD methods:
  - **Artists**: `insert_artist()`, `get_artist_by_id()`, `get_artist_by_plex_key()`, `update_artist()`, `delete_artist()`
  - **Albums**: `insert_album()`, `get_album_by_id()`, `get_album_by_plex_key()`, `update_album()`, `delete_album()`
  - **Tracks**: `insert_track()`, `get_track_by_id()`, `get_track_by_plex_key()`, `update_track()`, `delete_track()`, `get_all_tracks()`, `search_tracks_by_metadata(filters)`
  - **Genres**: `insert_genre()`, `get_genre_by_name()`, `get_all_genres()`
  - **Embeddings**: `insert_embedding()`, `get_embedding_by_track_id()`, `update_embedding()`, `delete_embedding()`, `get_all_embeddings()`
  - **Sync History**: `insert_sync_record()`, `get_latest_sync()`, `get_sync_history(limit)`
  - **Playlists**: `insert_playlist()`, `get_playlist_by_id()`, `add_track_to_playlist()`, `get_playlist_tracks()`, `delete_playlist()`

### TODO 1.5: Add Database Indexes for Performance
- [ ] Create indexes on frequently queried columns:
  - `tracks(artist_id)`, `tracks(album_id)`, `tracks(rating)`, `tracks(year)`, `tracks(genre)`
  - `albums(artist_id)`, `albums(year)`
  - `embeddings(track_id)`
  - `track_genres(track_id)`, `track_genres(genre_id)`
  - Unique indexes on `plex_key` fields

### TODO 1.6: Implement FTS5 Full-Text Search
- [ ] Create FTS5 virtual table for text search:
  - Include fields: track title, artist name, album title, genres
  - Implement `create_fts_table()` in SQLiteManager
  - Add methods: `search_tracks_fts(query)`, `rebuild_fts_index()`
- [ ] Create triggers to keep FTS5 in sync with main tables

### TODO 1.7: Add Database Migration Support
- [ ] Implement simple migration system:
  - `get_schema_version()` - Read current schema version
  - `set_schema_version(version)` - Update schema version
  - `migrate_database()` - Run migrations if needed
- [ ] Create migrations table to track applied migrations

---

## Phase 2: Plex Integration Layer

### TODO 2.1: Implement Plex Client Wrapper
- [ ] In `src/plexmix/plex/client.py`, create `PlexClient` class:
  - `__init__(url, token)` - Initialize PlexAPI connection
  - `connect()` - Establish connection and validate credentials
  - `test_connection()` - Verify server is reachable
  - `get_music_libraries()` - List all music libraries/sections
  - `select_library(name_or_index)` - Choose music library to sync
  - Handle connection errors with retry logic (exponential backoff)

### TODO 2.2: Implement Music Library Discovery
- [ ] Add methods to `PlexClient`:
  - `get_all_artists()` - Retrieve all artists with pagination
  - `get_all_albums()` - Retrieve all albums with pagination
  - `get_all_tracks()` - Retrieve all tracks with pagination
  - `get_artist_by_id(plex_id)` - Get single artist
  - `get_album_by_id(plex_id)` - Get single album
  - `get_track_by_id(plex_id)` - Get single track
  - Implement pagination to handle large libraries (batch size 100-500)

### TODO 2.3: Extract and Normalize Metadata
- [ ] Create metadata extraction methods:
  - `extract_artist_metadata(plex_artist)` - Convert Plex artist to Artist model
  - `extract_album_metadata(plex_album)` - Convert Plex album to Album model
  - `extract_track_metadata(plex_track)` - Convert Plex track to Track model
  - Handle missing/null fields gracefully
  - Normalize genre strings (lowercase, trim whitespace)
  - Extract all available metadata: duration, rating, play count, year, etc.

### TODO 2.4: Implement Rate Limiting
- [ ] Add rate limiting to Plex API calls:
  - Create `RateLimiter` utility class with token bucket algorithm
  - Wrap all Plex API methods with rate limiting decorator
  - Configure limits: max 10 requests per second (configurable)
  - Add exponential backoff on 429 responses

### TODO 2.5: Add Error Handling and Logging
- [ ] Implement robust error handling:
  - Catch network errors and retry with backoff
  - Handle authentication failures with clear messages
  - Log all Plex API interactions at DEBUG level
  - Provide user-friendly error messages for common issues (server unreachable, invalid token, library not found)

---

## Phase 3: Synchronization Engine

### TODO 3.1: Design Sync Strategy
- [ ] Document sync strategy:
  - **Full Sync**: Initial import of entire library
  - **Incremental Sync**: Only changes since last sync (new tracks, updated metadata, deleted tracks)
  - **Sync Modes**: `full`, `incremental`, `verify` (check consistency)
  - Track last sync timestamp in sync_history table

### TODO 3.2: Implement Full Sync
- [ ] In `src/plexmix/plex/sync.py`, create `SyncEngine` class:
  - `__init__(plex_client, db_manager)` - Initialize with dependencies
  - `full_sync()` - Complete library synchronization:
    1. Fetch all artists from Plex
    2. Insert/update artists in database
    3. Fetch all albums from Plex
    4. Insert/update albums in database
    5. Fetch all tracks from Plex
    6. Insert/update tracks in database
    7. Extract and store genres
    8. Create track-genre associations
    9. Record sync statistics in sync_history
  - Use transactions for atomicity
  - Display progress bar using Rich library

### TODO 3.3: Implement Incremental Sync
- [ ] Add incremental sync method:
  - `incremental_sync()` - Only sync changes:
    1. Get last sync timestamp from database
    2. Query Plex for items updated since last sync
    3. Identify new items, updated items, and deleted items
    4. Apply changes to database
    5. Update sync_history with statistics
  - Handle edge cases (first sync, interrupted sync)

### TODO 3.4: Implement Sync Verification
- [ ] Add verification method:
  - `verify_sync()` - Check database consistency:
    1. Compare counts (tracks, albums, artists) between Plex and database
    2. Identify orphaned records (tracks without albums/artists)
    3. Check for missing Plex keys
    4. Report discrepancies with detailed logging
    5. Offer repair options

### TODO 3.5: Add Conflict Resolution
- [ ] Implement conflict handling:
  - When track exists in DB but metadata differs from Plex:
    - Always use Plex as source of truth
    - Update local database with Plex values
    - Log metadata changes at INFO level
  - When track exists in DB but deleted from Plex:
    - **Hard delete**: Remove from database entirely
    - Also remove associated embeddings and playlist references

### TODO 3.6: Batch Processing and Performance
- [ ] Optimize sync performance:
  - Batch database inserts (100-500 records per transaction)
  - Use prepared statements for repeated queries
  - Implement parallel fetching from Plex (thread pool, 3-5 workers)
  - Add progress tracking with estimated time remaining

---

## Phase 4: Embedding Generation & Vector Index

### TODO 4.1: Configure Embedding Strategy
- [ ] Implement embedding approach:
  - **Default Provider**: Google Gemini `gemini-embedding-001` (3072 dims) - Only requires Google API key
  - **Alternative Providers**:
    - OpenAI `text-embedding-3-small` (1536 dims, requires OpenAI API key)
    - Local model via `sentence-transformers` (384-768 dims, free, offline)
  - Support all providers, selectable via configuration
  - **Embedding text format**: `"{title} by {artist} from {album} - {genres} ({year})"`
  - Example: `"Bohemian Rhapsody by Queen from A Night at the Opera - rock, classic rock (1975)"`
  - **Key benefit**: Default setup only requires single Google API key for both AI and embeddings

### TODO 4.2: Implement Embedding Generator
- [ ] In `src/plexmix/utils/embeddings.py`, create `EmbeddingGenerator` class:
  - `__init__(provider, model)` - Initialize with provider (gemini/openai/local)
  - `generate_embedding(text)` - Generate single embedding vector
  - `generate_batch_embeddings(texts)` - Batch generation for efficiency
  - Handle API errors and retries
  - Add caching to avoid regenerating same embeddings
  - Support all three providers:
    - **Google Gemini**: `gemini-embedding-001` (3072 dims) - default
    - **OpenAI**: `text-embedding-3-small` (1536 dims)
    - **Local**: sentence-transformers models (384-768 dims)

### TODO 4.3: Create Track Embedding Pipeline
- [ ] In `src/plexmix/utils/embeddings.py`, add:
  - `create_track_text(track)` - Format track metadata into embedding text using format: `"{title} by {artist} from {album} - {genres} ({year})"`
  - `embed_track(track)` - Generate embedding for single track
  - `embed_all_tracks(tracks, batch_size=100)` - Process all tracks:
    1. Create text representations
    2. Generate embeddings in batches
    3. Store in embeddings table
    4. Show progress bar
  - Handle embedding failures gracefully (skip and log)

### TODO 4.4: Implement FAISS Vector Index
- [ ] In `src/plexmix/database/vector_index.py`, create `VectorIndex` class:
  - `__init__(dimension, index_path)` - Initialize FAISS index (IndexFlatIP for cosine similarity)
  - **Note**: Dimension varies by provider (3072 for Gemini default, 1536 for OpenAI, 384-768 for local)
  - `build_index(embeddings, track_ids)` - Build FAISS index from embeddings:
    1. Convert embeddings to numpy array (float32)
    2. Normalize vectors for cosine similarity
    3. Create FAISS index
    4. Store track_id mapping (list or separate file)
  - `save_index(path)` - Persist index to disk
  - `load_index(path)` - Load index from disk
  - `add_vectors(embeddings, track_ids)` - Add new vectors to existing index
  - `remove_vectors(track_ids)` - Remove vectors (requires rebuild)

### TODO 4.5: Implement Vector Search
- [ ] Add search methods to `VectorIndex`:
  - `search(query_vector, k=25, filters=None)` - Find k nearest neighbors:
    1. Normalize query vector
    2. Perform FAISS similarity search
    3. Return track IDs with similarity scores
  - `search_by_text(query_text, embedding_generator, k=25)` - Text query:
    1. Generate embedding for query text
    2. Search using query vector
    3. Return results
  - Handle empty index gracefully

### TODO 4.6: Integrate Embeddings into Sync Process
- [ ] Update `SyncEngine` to generate embeddings:
  - After syncing tracks, check which tracks need embeddings
  - Generate embeddings for new/updated tracks
  - Update embeddings table
  - Rebuild FAISS index with new embeddings
  - Make embedding generation optional/configurable (can defer to first use)

### TODO 4.7: Add Embedding Cache and Versioning
- [ ] Implement embedding cache:
  - Store embedding model name and version in embeddings table
  - Only regenerate if model changes
  - Provide command to regenerate all embeddings (e.g., model upgrade)
  - Add `rebuild_embeddings()` method to sync engine

---

## Phase 5: AI Provider Integration

### TODO 5.1: Design AI Provider Interface
- [ ] In `src/plexmix/ai/base.py`, create abstract base class `AIProvider`:
  - Define interface methods:
    - `generate_playlist(mood_query, candidate_tracks, max_tracks=25)` - Main method
    - `_prepare_prompt(mood_query, tracks)` - Format prompt for LLM
    - `_parse_response(response)` - Extract track selections from LLM response
    - `_validate_selections(selections, candidate_tracks)` - Ensure tracks exist
  - Define expected return format (list of track IDs with confidence scores)

### TODO 5.2: Implement OpenAI Provider
- [ ] In `src/plexmix/ai/openai_provider.py`, create `OpenAIProvider(AIProvider)`:
  - `__init__(api_key, model="gpt-4o-mini")` - Initialize OpenAI client
  - `generate_playlist(mood_query, candidate_tracks, max_tracks)`:
    1. Prepare system prompt explaining task
    2. Format candidate tracks as JSON (id, title, artist, album, genres, year)
    3. Include user mood query
    4. Request structured JSON response with track IDs
    5. Parse response and extract track selections
    6. Validate selections exist in candidate list
    7. Return ordered list of track IDs
  - Handle API errors (rate limits, invalid responses)
  - Implement token budget management (truncate candidates if needed)

### TODO 5.3: Implement Google Gemini Provider (Default)
- [ ] In `src/plexmix/ai/gemini_provider.py`, create `GeminiProvider(AIProvider)`:
  - `__init__(api_key, model="gemini-2.5-flash")` - Initialize Gemini client with latest flash model
  - Implement same interface as OpenAI provider
  - Adjust prompt format for Gemini's response style
  - Handle Gemini-specific API quirks
  - **This is the default AI provider for playlist generation**

### TODO 5.4: Implement Anthropic Claude Provider
- [ ] In `src/plexmix/ai/claude_provider.py`, create `ClaudeProvider(AIProvider)`:
  - `__init__(api_key, model="claude-3-5-sonnet-20241022")` - Initialize Anthropic client
  - Implement same interface as OpenAI provider
  - Adjust prompt format for Claude's response style
  - Handle Claude-specific API requirements

### TODO 5.5: Create AI Provider Factory
- [ ] In `src/plexmix/ai/__init__.py`, create factory function:
  - `get_ai_provider(provider_name, api_key, model=None)` - Return appropriate provider instance:
    - Map "openai" → OpenAIProvider
    - Map "gemini" → GeminiProvider
    - Map "claude" → ClaudeProvider
    - Validate provider name
    - Load API key from environment if not provided
    - Use default model if not specified

### TODO 5.6: Implement Prompt Engineering
- [ ] Design effective prompts for playlist generation:
  - **System Prompt**: Explain role as music curator, output format requirements
  - **Context**: Provide candidate tracks with metadata
  - **Task**: Interpret mood query and select appropriate tracks
  - **Constraints**: Select exactly N tracks, must be from provided list, order by relevance
  - **Output Format**: JSON array of track IDs with optional reasoning
  - Test prompts with various mood queries to ensure quality

### TODO 5.7: Add Response Validation and Fallbacks
- [ ] Implement robust response handling:
  - Validate LLM returns valid JSON
  - Check all track IDs exist in candidate list
  - Handle partial responses (fewer tracks than requested)
  - Implement fallback strategies:
    - If LLM fails, use top-K from vector search
    - If LLM returns invalid IDs, filter and use valid ones
    - Retry with modified prompt on certain errors
  - Log all LLM interactions for debugging

---

## Phase 6: Playlist Generation Engine

### TODO 6.1: Design Playlist Generation Workflow
- [ ] Document the playlist generation pipeline:
  1. Parse user mood query
  2. Apply optional SQL filters (genre, year, rating)
  3. Generate query embedding
  4. Perform FAISS similarity search (retrieve top-K candidates, e.g., 100)
  5. Optionally apply FTS5 keyword filtering
  6. Feed candidates to AI provider
  7. AI selects final N tracks (e.g., 25)
  8. Validate and deduplicate
  9. Create playlist in Plex
  10. Save playlist metadata to database

### TODO 6.2: Implement Playlist Generator Core
- [ ] In `src/plexmix/playlist/generator.py`, create `PlaylistGenerator` class:
  - `__init__(db_manager, vector_index, ai_provider, embedding_generator)` - Initialize dependencies
  - `generate(mood_query, filters=None, max_tracks=25, candidate_pool_size=100)` - Main generation method:
    1. Apply SQL filters if provided (genre, year, rating, artist)
    2. Get filtered track IDs from database
    3. Generate embedding for mood query
    4. Perform vector search on filtered tracks (or all if no filters)
    5. Retrieve top candidate_pool_size tracks
    6. Format candidate metadata for AI
    7. Call AI provider to select final tracks
    8. Validate and order results
    9. Return playlist track list

### TODO 6.3: Implement Query Parsing and Filters
- [ ] Add filter parsing methods:
  - `parse_filters(mood_query)` - Extract filters from natural language:
    - Detect genre mentions ("jazz", "rock")
    - Detect year ranges ("from 2020", "90s music")
    - Detect rating requirements ("highly rated", "5 stars")
    - Return structured filter dict
  - `apply_sql_filters(filters)` - Convert to SQL WHERE clause
  - Make filter parsing optional (can be explicit via CLI args)

### TODO 6.4: Implement Candidate Retrieval
- [ ] Add candidate retrieval logic:
  - `get_candidates(mood_query, filters, pool_size)`:
    1. Generate embedding for mood query
    2. If filters present, get filtered track IDs from SQLite
    3. Search FAISS index (constrained to filtered IDs if applicable)
    4. Retrieve top pool_size results
    5. Fetch full track metadata from database
    6. Return candidate tracks with similarity scores

### TODO 6.5: Add Deduplication and Validation
- [ ] Implement validation methods:
  - `deduplicate_tracks(track_ids)` - Remove duplicate track IDs
  - `validate_tracks(track_ids)` - Ensure all tracks exist in database
  - `remove_recently_played(track_ids, days=7)` - Optional: avoid recently played tracks
  - `ensure_diversity(tracks)` - Optional: limit tracks from same artist/album

### TODO 6.6: Implement Plex Playlist Creation
- [ ] Add Plex integration to playlist generator:
  - `create_plex_playlist(playlist_name, track_ids, description=None)`:
    1. Connect to Plex using PlexClient
    2. Retrieve Plex track objects for track IDs
    3. Create playlist via Plex API
    4. Set playlist metadata (name, description)
    5. Return Plex playlist object
  - Handle errors (invalid tracks, Plex connection issues)

### TODO 6.7: Save Playlist Metadata
- [ ] Add database persistence:
  - `save_playlist_metadata(name, description, mood_query, track_ids, plex_key)`:
    1. Insert playlist record into playlists table
    2. Insert track associations into playlist_tracks table
    3. Store mood query for future reference
    4. Mark as AI-generated with timestamp
    5. Return playlist ID

---

## Phase 7: Configuration Management

### TODO 7.1: Design Configuration Structure
- [ ] Define configuration hierarchy:
  - **Environment variables** (highest priority): API keys, sensitive data
  - **Config file** (`~/.plexmix/config.yaml`): User preferences
  - **CLI arguments** (override config file): Per-command options
  - **Defaults** (lowest priority): Sensible defaults in code

### TODO 7.2: Implement Settings Model
- [ ] In `src/plexmix/config/settings.py`, create Pydantic settings:
  - `PlexSettings` - URL, token, library name
  - `DatabaseSettings` - SQLite path (default: `~/.plexmix/plexmix.db`), FAISS index path (default: `~/.plexmix/embeddings.index`)
  - `AISettings` - Default provider (default: `gemini`), model, temperature, API keys
  - `EmbeddingSettings` - Provider (default: `gemini`), model (default: `gemini-embedding-001`), dimension (3072)
  - `PlaylistSettings` - Default length (50), candidate pool size (100)
  - `LoggingSettings` - Level, format, file path (default: `~/.plexmix/plexmix.log`)
  - Use Pydantic's `BaseSettings` with env var support
  - **Note**: Embedding dimension must match provider (3072 for Gemini, 1536 for OpenAI, varies for local)

### TODO 7.3: Implement Config File Management
- [ ] Create config file utilities:
  - `load_config(path)` - Load YAML config file
  - `save_config(config, path)` - Save config to YAML
  - `get_config_path()` - Return default config path (`~/.plexmix/config.yaml`)
  - `ensure_config_dir()` - Create config directory if missing
  - `create_default_config()` - Generate template config file

### TODO 7.4: Implement Credential Management
- [ ] In `src/plexmix/config/credentials.py`, use keyring:
  - `store_credential(service, key, value)` - Store in system keyring
  - `get_credential(service, key)` - Retrieve from keyring
  - `delete_credential(service, key)` - Remove credential
  - Services: "plexmix-plex", "plexmix-openai", "plexmix-gemini", "plexmix-claude"
  - Fallback to env vars if keyring unavailable

### TODO 7.5: Create Configuration Validator
- [ ] Add validation methods:
  - `validate_plex_config(config)` - Test Plex connection
  - `validate_ai_config(config)` - Test AI provider API keys
  - `validate_paths(config)` - Check database and index paths are writable
  - `validate_all(config)` - Run all validators and report issues

### TODO 7.6: Implement First-Run Setup Wizard
- [ ] Create interactive setup:
  - `run_setup_wizard()` - Interactive CLI wizard:
    1. Welcome message and MIT license notice
    2. Prompt for Plex URL and token (with instructions on how to get token)
    3. Test Plex connection
    4. List music libraries and let user select
    5. **Prompt for Google Gemini API key** (required for both default AI provider and embeddings)
    6. Inform user: "With just the Google API key, you can use both AI playlist generation and embeddings"
    7. Optionally prompt: "Would you like to configure alternative providers?" (OpenAI, Anthropic, local embeddings)
    8. If yes, prompt for OpenAI API key (optional, for alternative embeddings/AI)
    9. If yes, prompt for Anthropic Claude API key (optional, for alternative AI)
    10. Store API keys in system keyring
    11. Confirm database location (default: `~/.plexmix/`)
    12. Save configuration to `~/.plexmix/config.yaml`
    13. **Prompt user**: Run initial sync now or defer? (Warning: may take 10-30 minutes for large libraries)
    14. If user confirms, run `sync full` with progress tracking
  - Use Rich prompts for user input
  - Provide helpful instructions and examples throughout
  - **Emphasize simplicity**: Single Google API key gets everything working

---

## Phase 8: CLI Interface

### TODO 8.1: Design Command Structure
- [ ] Plan CLI commands and subcommands:
  - `plexmix config` - Configuration management
    - `config init` - Run setup wizard
    - `config show` - Display current config
    - `config set <key> <value>` - Update config value
    - `config validate` - Test configuration
  - `plexmix sync` - Library synchronization
    - `sync full` - Full library sync
    - `sync incremental` - Incremental sync
    - `sync verify` - Verify database consistency
    - `sync status` - Show last sync info
  - `plexmix create <mood>` - Create playlist
    - Options: `--provider` (default: gemini), `--limit` (prompt user, default: 50), `--name`, `--genre`, `--year`, `--rating`
    - Interactive: Prompt user for playlist length before generation
  - `plexmix list` - List playlists
    - `list playlists` - Show all playlists
    - `list tracks <playlist_id>` - Show tracks in playlist
  - `plexmix search <query>` - Search music library
  - `plexmix embeddings` - Manage embeddings
    - `embeddings generate` - Generate embeddings for all tracks
    - `embeddings rebuild` - Regenerate all embeddings
    - `embeddings status` - Show embedding statistics

### TODO 8.2: Implement Main CLI Entry Point
- [ ] In `src/plexmix/cli/main.py`, create Typer app:
  - Define main application with name, version, help text
  - Add global options: `--config`, `--verbose`, `--quiet`
  - Setup logging based on verbosity
  - Load configuration from `~/.plexmix/config.yaml`
  - Handle keyboard interrupts gracefully (Ctrl+C)
  - Display MIT license info in help text

### TODO 8.3: Implement Config Commands
- [ ] Add config command group:
  - `config_init()` - Run setup wizard
  - `config_show()` - Display formatted config (hide sensitive values)
  - `config_set(key, value)` - Update config value and save
  - `config_validate()` - Run validators and display results

### TODO 8.4: Implement Sync Commands
- [ ] Add sync command group:
  - `sync_full()` - Run full sync:
    1. Initialize PlexClient and SQLiteManager
    2. Create SyncEngine
    3. Run full_sync() with progress bar
    4. Display sync statistics
    5. Optionally generate embeddings
  - `sync_incremental()` - Run incremental sync
  - `sync_verify()` - Run verification and display report
  - `sync_status()` - Query sync_history and display last sync info

### TODO 8.5: Implement Create Playlist Command
- [ ] Add playlist creation command:
  - `create_playlist(mood, provider, limit, name, genre, year, rating)`:
    1. Load configuration and initialize components
    2. **Prompt user for playlist length** (if --limit not provided, default: 50)
    3. Build filter dict from options
    4. Initialize PlaylistGenerator (default provider: gemini)
    5. Generate playlist tracks
    6. Display track list with rich table
    7. Prompt for playlist name (default: mood query + timestamp)
    8. Create playlist in Plex
    9. Save to database
    10. Display success message with Plex link
  - Add `--dry-run` flag to preview without creating
  - Add `--no-plex` flag to skip Plex creation (local only)
  - Default AI provider: Google Gemini (gemini-2.5-flash)
  - Allow override with `--provider openai` or `--provider claude`

### TODO 8.6: Implement List Commands
- [ ] Add list command group:
  - `list_playlists()` - Query database and display playlists table:
    - Columns: ID, Name, Tracks, Mood Query, Created At
    - Use Rich table formatting
  - `list_tracks(playlist_id)` - Display tracks in playlist:
    - Columns: Position, Title, Artist, Album, Duration
    - Show playlist metadata at top

### TODO 8.7: Implement Search Command
- [ ] Add search functionality:
  - `search(query, limit)` - Search library:
    1. Perform FTS5 search for keywords
    2. Perform vector similarity search for semantic matches
    3. Combine and rank results
    4. Display formatted table with scores
  - Add filter options (genre, year, artist)

### TODO 8.8: Implement Embeddings Commands
- [ ] Add embeddings command group:
  - `embeddings_generate()` - Generate embeddings for tracks without them
  - `embeddings_rebuild()` - Regenerate all embeddings (confirm prompt)
  - `embeddings_status()` - Display statistics:
    - Total tracks
    - Tracks with embeddings
    - Embedding model/version
    - FAISS index status

### TODO 8.9: Add Rich Console Formatting
- [ ] Enhance CLI output with Rich:
  - Use `rich.console.Console` for all output
  - Add progress bars for long operations (sync, embedding generation)
  - Use tables for structured data (playlists, tracks, search results)
  - Add status spinners for API calls
  - Use panels for grouped information
  - Add colors and styling for errors, warnings, success messages

### TODO 8.10: Implement Error Handling and User Feedback
- [ ] Add comprehensive error handling:
  - Catch exceptions and display user-friendly messages
  - Log detailed errors at DEBUG level
  - Provide actionable suggestions for common issues
  - Handle Ctrl+C gracefully with cleanup
  - Add `--debug` flag for verbose error output

---

## Phase 9: Logging and Monitoring

### TODO 9.1: Configure Logging Infrastructure
- [ ] In `src/plexmix/utils/logging.py`, setup logging:
  - Configure Python logging with appropriate handlers
  - **Console Handler**: INFO and above (or based on --verbose flag)
  - **File Handler**: DEBUG and above to `~/.plexmix/plexmix.log`
  - Use structured log format with timestamps
  - Implement log rotation (max 10MB, keep 5 backups)

### TODO 9.2: Add Logging to All Modules
- [ ] Add logger instances to each module:
  - PlexClient: Log API calls, connection status, errors
  - SyncEngine: Log sync progress, statistics, errors
  - Database: Log queries (at DEBUG), errors
  - VectorIndex: Log index operations, search queries
  - AIProvider: Log LLM calls, token usage, responses
  - PlaylistGenerator: Log generation steps, results

### TODO 9.3: Implement Performance Metrics
- [ ] Add timing and metrics logging:
  - Track sync duration
  - Track embedding generation time
  - Track FAISS search latency
  - Track LLM API call duration
  - Log metrics at INFO level after operations

### TODO 9.4: Add Debug Mode
- [ ] Implement enhanced debugging:
  - `--debug` flag sets log level to DEBUG
  - Log full API requests/responses
  - Log SQL queries
  - Save LLM prompts and responses to debug files
  - Add memory profiling option

---

## Phase 10: Testing

### TODO 10.1: Setup Testing Infrastructure
- [ ] Configure pytest:
  - Create `pytest.ini` or `pyproject.toml` test config
  - Setup test fixtures for database, Plex mock, AI mock
  - Configure coverage reporting (aim for >80%)
  - Create test data fixtures (sample tracks, albums, artists)

### TODO 10.2: Write Database Tests
- [ ] In `tests/test_database.py`:
  - Test SQLiteManager initialization
  - Test table creation
  - Test CRUD operations for all entities
  - Test FTS5 search functionality
  - Test transaction rollback on errors
  - Test index creation and queries
  - Mock database for unit tests

### TODO 10.3: Write Plex Integration Tests
- [ ] In `tests/test_plex.py`:
  - Mock PlexAPI responses
  - Test connection and authentication
  - Test metadata extraction
  - Test rate limiting
  - Test error handling (network errors, auth failures)
  - Test pagination

### TODO 10.4: Write Embedding and Vector Tests
- [ ] In `tests/test_embeddings.py`:
  - Test embedding generation (mock OpenAI API)
  - Test FAISS index creation and search
  - Test vector normalization
  - Test batch processing
  - Test index persistence (save/load)

### TODO 10.5: Write AI Provider Tests
- [ ] In `tests/test_ai.py`:
  - Mock LLM API responses for each provider
  - Test prompt formatting
  - Test response parsing
  - Test error handling and retries
  - Test token budget management

### TODO 10.6: Write Playlist Generation Tests
- [ ] In `tests/test_playlist.py`:
  - Test end-to-end playlist generation (mocked)
  - Test filter parsing
  - Test candidate retrieval
  - Test deduplication
  - Test Plex playlist creation (mocked)

### TODO 10.7: Write CLI Tests
- [ ] In `tests/test_cli.py`:
  - Use Click/Typer testing utilities
  - Test all CLI commands with various arguments
  - Test error messages and exit codes
  - Test interactive prompts (if applicable)

### TODO 10.8: Add Integration Tests
- [ ] Create integration test suite:
  - Test full sync workflow with mock Plex server
  - Test end-to-end playlist creation
  - Test configuration loading and validation
  - Use temporary databases and indexes
  - Clean up test artifacts

---

## Phase 11: Documentation

### TODO 11.1: Write README.md
- [ ] Create comprehensive README:
  - Project description and features
  - Screenshots/demo (optional)
  - **MIT License badge** at top
  - Installation instructions (pip install plexmix or poetry install)
  - Quick start guide (run `plexmix config init` first)
  - **Highlight**: "Only requires a Google API key to get started!"
  - Configuration guide (Plex token, Google API key)
  - Default settings explanation (Gemini AI, Gemini embeddings)
  - Optional providers (OpenAI, Claude, local embeddings)
  - Usage examples for each command
  - Troubleshooting section
  - Contributing guidelines
  - MIT License information

### TODO 11.2: Write User Documentation
- [ ] In `docs/` directory:
  - `installation.md` - Detailed installation steps
  - `configuration.md` - Configuration file reference
  - `commands.md` - CLI command reference with examples
  - `ai-providers.md` - AI provider setup and comparison
  - `troubleshooting.md` - Common issues and solutions
  - `faq.md` - Frequently asked questions

### TODO 11.3: Write Developer Documentation
- [ ] Create developer docs:
  - `architecture.md` - System architecture overview
  - `database-schema.md` - Database schema documentation
  - `api-reference.md` - Internal API documentation
  - `contributing.md` - How to contribute, code style, PR process
  - `testing.md` - How to run tests, writing tests

### TODO 11.4: Add Inline Documentation
- [ ] Document code with docstrings:
  - Add docstrings to all classes (Google/NumPy style)
  - Add docstrings to all public methods
  - Document complex algorithms inline
  - Add type hints to all function signatures
  - Generate API docs with Sphinx (optional)

### TODO 11.5: Create Example Configurations
- [ ] Add example files:
  - `examples/config.yaml.example` - Annotated config example
  - `examples/mood-queries.md` - Effective mood query examples
  - `examples/advanced-filters.md` - Advanced filter usage

---

## Phase 12: Packaging and Distribution

### TODO 12.1: Configure Poetry Build
- [ ] Update `pyproject.toml` for packaging:
  - Set entry point: `plexmix = plexmix.cli.main:app`
  - Include package data (if any)
  - Set classifiers (Python versions: >=3.10, license: MIT, development status, etc.)
  - Define package description and keywords
  - Set license field to "MIT"
  - Add README.md as project description source

### TODO 12.2: Test Local Installation
- [ ] Test package installation:
  - Build wheel: `poetry build`
  - Install locally: `pip install dist/plexmix-*.whl`
  - Test CLI commands work as expected
  - Test on clean virtual environment

### TODO 12.3: Create Release Workflow
- [ ] Setup release process:
  - Use semantic versioning (MAJOR.MINOR.PATCH)
  - Create GitHub releases with changelog
  - Tag releases in git
  - Document release process

### TODO 12.4: Publish to PyPI (Optional)
- [ ] Prepare for PyPI publication:
  - Create PyPI account
  - Configure poetry credentials
  - Test on TestPyPI first
  - Publish with `poetry publish`
  - Verify installation: `pip install plexmix`

### TODO 12.5: Setup CI/CD (Optional)
- [ ] Create GitHub Actions workflows:
  - Run tests on pull requests
  - Check code formatting and linting
  - Build and test package
  - Auto-publish releases to PyPI

---

## Phase 13: Advanced Features (Future Enhancements)

### TODO 13.1: Multi-Library Support
- [ ] Add support for multiple Plex libraries:
  - Track library association in database
  - Allow library selection in sync and create commands
  - Support multiple Plex servers

### TODO 13.2: Playlist Templates
- [ ] Implement playlist templates:
  - Save favorite mood queries as templates
  - Allow parameterized templates (genre, era, etc.)
  - Quick playlist creation from templates

### TODO 13.3: Smart Shuffle and Ordering
- [ ] Enhance playlist ordering:
  - Smart shuffle (avoid consecutive tracks from same album/artist)
  - Energy-based ordering (start high energy, wind down)
  - BPM-based transitions (if available)

### TODO 13.4: User Listening History
- [ ] Track user preferences:
  - Log playlist plays and skips
  - Learn from user feedback
  - Improve recommendations over time

### TODO 13.5: Web Interface (Optional)
- [ ] Create web UI:
  - FastAPI backend exposing REST API
  - Simple web frontend for playlist creation
  - Browse library and playlists
  - Hosted mode for multi-user access

### TODO 13.6: Export/Import Playlists
- [ ] Add playlist portability:
  - Export playlists to M3U, JSON
  - Import external playlists
  - Sync playlists between Plex servers

### TODO 13.7: Audio Feature Analysis (Optional)
- [ ] Integrate audio analysis:
  - Use Spotify API or local audio analysis
  - Extract tempo, key, energy, danceability
  - Store as additional metadata
  - Use in playlist generation (upbeat = high energy + tempo)

---

## Implementation Checklist Summary

### Phase 0: Foundation
- [ ] Project structure created
- [ ] Dependencies configured
- [ ] Environment setup complete
- [ ] Git repository initialized

### Phase 1: Database
- [ ] SQLite schema designed and implemented
- [ ] Pydantic models created
- [ ] CRUD operations implemented
- [ ] FTS5 search configured
- [ ] Indexes optimized

### Phase 2: Plex Integration
- [ ] PlexClient wrapper implemented
- [ ] Metadata extraction working
- [ ] Rate limiting configured
- [ ] Error handling robust

### Phase 3: Synchronization
- [ ] Full sync implemented
- [ ] Incremental sync working
- [ ] Verification tool created
- [ ] Performance optimized

### Phase 4: Embeddings & Vectors
- [ ] Embedding generation implemented
- [ ] FAISS index operational
- [ ] Vector search working
- [ ] Index persistence configured

### Phase 5: AI Providers
- [ ] Base provider interface defined
- [ ] OpenAI provider implemented
- [ ] Gemini provider implemented
- [ ] Claude provider implemented
- [ ] Response validation robust

### Phase 6: Playlist Generation
- [ ] Core generation engine working
- [ ] Filter parsing implemented
- [ ] Candidate retrieval optimized
- [ ] Plex playlist creation functional
- [ ] Database persistence working

### Phase 7: Configuration
- [ ] Settings model implemented
- [ ] Config file management working
- [ ] Credential storage secure
- [ ] Setup wizard functional

### Phase 8: CLI
- [ ] All commands implemented
- [ ] Rich formatting applied
- [ ] Error handling comprehensive
- [ ] User experience polished

### Phase 9: Logging
- [ ] Logging configured
- [ ] Metrics tracked
- [ ] Debug mode functional

### Phase 10: Testing
- [ ] Unit tests written (>80% coverage)
- [ ] Integration tests passing
- [ ] Mocking comprehensive

### Phase 11: Documentation
- [ ] README complete
- [ ] User docs written
- [ ] Developer docs available
- [ ] Code documented

### Phase 12: Distribution
- [ ] Package buildable
- [ ] PyPI ready (optional)
- [ ] CI/CD configured (optional)

---

## Notes for Implementation

### Best Practices
1. **Incremental Development**: Implement and test each phase before moving to the next
2. **Test-Driven**: Write tests alongside implementation, not after
3. **Logging First**: Add logging early to aid debugging
4. **User Feedback**: Use Rich library for progress bars on long operations
5. **Error Messages**: Provide actionable error messages with suggestions
6. **Configuration Validation**: Validate config early to catch issues before operations
7. **Graceful Degradation**: Handle missing optional features (e.g., no API key for AI provider)

### Performance Considerations
- Batch database operations (100-500 records per transaction)
- Use connection pooling for database access
- Cache embeddings aggressively
- Implement lazy loading for large datasets
- Profile slow operations and optimize

### Security Considerations
- Never log API keys or tokens
- Use keyring for credential storage
- Validate all user inputs
- Use parameterized SQL queries (prevent injection)
- Handle file paths securely (prevent path traversal)

### Code Quality
- Follow PEP 8 style guide
- Use type hints throughout
- Keep functions small and focused (single responsibility)
- Write descriptive variable and function names
- Add comments for complex logic
- Use meaningful commit messages

---

## Timeline Estimate

- **Phase 0**: 1 day
- **Phase 1**: 2-3 days
- **Phase 2**: 2 days
- **Phase 3**: 2-3 days
- **Phase 4**: 3-4 days
- **Phase 5**: 2-3 days
- **Phase 6**: 2-3 days
- **Phase 7**: 1-2 days
- **Phase 8**: 3-4 days
- **Phase 9**: 1 day
- **Phase 10**: 3-4 days
- **Phase 11**: 2 days
- **Phase 12**: 1 day

**Total Estimated Time**: 25-35 days (solo developer, full-time)

---

## Success Criteria

The project is considered complete when:
1. User can sync Plex library to local database
2. Embeddings are generated for all tracks
3. User can create playlists via CLI with mood queries
4. AI providers successfully generate relevant playlists
5. Playlists are created in Plex server
6. All core commands work reliably
7. Documentation is comprehensive
8. Test coverage >80%
9. Package is installable via pip

---

## Getting Help

When implementing this plan:
- Refer to library documentation (PlexAPI, FAISS, OpenAI, etc.)
- Test each component in isolation before integration
- Use logging extensively to understand program flow
- Ask for help with specific issues (not entire phases)
- Break down large tasks into smaller, manageable pieces

Good luck building PlexMix!
