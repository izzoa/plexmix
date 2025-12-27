# Changelog

All notable changes to PlexMix will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Loading interstitial overlay during page navigation for improved UX

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

[Unreleased]: https://github.com/izzoa/plexmix/compare/v0.3.0...HEAD
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
