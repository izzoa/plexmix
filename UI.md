# PlexMix Reflex UI Implementation Plan

## Overview

This document provides a complete implementation plan for adding a Reflex-based web UI to PlexMix. Each phase is broken down into actionable TODO items that can be followed to coding completion.

---

## Architecture Overview

```
+-------------------------------------------------------+
|            Reflex Web UI (Browser)                    |
|  Dashboard | Generator | Library | Tagging | History  |
+-------------------------------------------------------+
                         ^ v
              Reflex State Management
           (Hybrid: AppState + Page States)
                         ^ v
+-------------------------------------------------------+
|         Reflex Background Tasks (@rx.background)      |
|  Async generators with progress updates via yield    |
+-------------------------------------------------------+
                         ^ v
+-------------------------------------------------------+
|           Existing PlexMix Modules (Shared)           |
|  PlexClient | SQLiteManager | AI Providers | etc.    |
+-------------------------------------------------------+
                         ^ v
+-------------------------------------------------------+
|              Data Layer (Shared)                      |
|  SQLite DB | FAISS Index | Keyring Config            |
+-------------------------------------------------------+
```

## Key Design Principles

1. **No Breaking Changes**: All modifications to existing code are backward-compatible
2. **Code Reuse**: Leverage all existing PlexMix modules directly
3. **Shared Config**: CLI and UI use the same configuration system
4. **Progressive Enhancement**: UI is an optional add-on, CLI remains fully functional
5. **Background Tasks**: Use Reflex's native @rx.background decorator for long operations
6. **Hybrid State Management**: Shared AppState for global data, page-specific states for local concerns

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| State Management | Hybrid (AppState + Page States) | Share common data, isolate page concerns |
| Background Tasks | Reflex @rx.background with async generators | Native support, clean API, real-time updates |
| Database Access | Read-direct, Write-queue pattern | Safe for SQLite's threading model |
| Component Library | Reflex + Chakra UI | Professional look, built-in support |
| Performance | Pagination + debounced search + SQL filtering | Handles 10K+ tracks smoothly |

---

# Implementation Phases

## Phase 1: Foundation & Setup

### Goal
Set up Reflex infrastructure, basic routing, and shared components.

### TODO List

#### 1.1 Environment & Dependencies
- [ ] Add Reflex dependency to `pyproject.toml` under `[tool.poetry.dependencies]`
- [ ] Create optional extras group in `pyproject.toml` for UI dependencies
- [ ] Run poetry lock and install with UI extras

#### 1.2 Directory Structure
- [ ] Create `src/plexmix/ui/` directory
- [ ] Create `src/plexmix/ui/pages/` directory
- [ ] Create `src/plexmix/ui/states/` directory
- [ ] Create `src/plexmix/ui/components/` directory
- [ ] Create `src/plexmix/ui/utils/` directory
- [ ] Create `tests/ui/` directory for UI tests
- [ ] Create `assets/` directory in project root for static assets
- [ ] Create `__init__.py` files in all new Python directories

#### 1.3 Minimal Reflex App
- [ ] Create `src/plexmix/ui/app.py` as main Reflex entry point
- [ ] Define basic Reflex app instance with theme configuration
- [ ] Create simple index page component with welcome message
- [ ] Create placeholder dashboard page component
- [ ] Add routes for both pages to the app
- [ ] Test app runs with `reflex run` command

#### 1.4 CLI Integration
- [ ] Add `ui` command to `src/plexmix/cli/main.py`
- [ ] Command should accept host, port, and reload options
- [ ] Import and run Reflex app when command is invoked
- [ ] Add graceful error handling if Reflex is not installed
- [ ] Provide helpful message directing users to install UI extras
- [ ] Test command with `plexmix ui`

#### 1.5 Shared Navigation Component
- [ ] Create `src/plexmix/ui/components/navbar.py`
- [ ] Define navbar_link helper function for menu items
- [ ] Create navbar component with sidebar layout
- [ ] Include links for all 6 main pages (Dashboard, Generate, Library, Tagging, History, Settings)
- [ ] Add PlexMix logo/heading to navbar
- [ ] Style navbar with fixed position, background color, spacing
- [ ] Create layout wrapper function that includes navbar and content area
- [ ] Set content area margin to account for fixed navbar width

#### 1.6 Shared UI Components
- [ ] Create `src/plexmix/ui/components/progress_modal.py`
- [ ] Define modal component that accepts progress value and message
- [ ] Include progress bar, status message, and cancel button
- [ ] Make modal reusable for any background task
- [ ] Create `src/plexmix/ui/components/toast.py`
- [ ] Define toast notification system for success/error/warning messages
- [ ] Support different color schemes based on message type
- [ ] Include auto-dismiss functionality

#### 1.7 Testing & Validation
- [ ] Verify `plexmix ui` launches dev server
- [ ] Confirm navigation between index and dashboard pages works
- [ ] Check navbar appears on all pages
- [ ] Validate all navigation links are present and styled correctly
- [ ] Test dev server hot-reload functionality

**Deliverable**: Basic running Reflex app with navigation

---

## Phase 2: Dashboard & Settings

### Goal
Implement configuration pages and overview dashboard.

### TODO List

#### 2.1 AppState - Shared Global State
- [ ] Create `src/plexmix/ui/states/app_state.py`
- [ ] Define AppState class inheriting from rx.State
- [ ] Add state variables for config status (plex_configured, ai_provider_configured, embedding_provider_configured)
- [ ] Add state variables for library stats (total_tracks, embedded_tracks, last_sync)
- [ ] Add state variables for background task tracking (current_task, task_progress)
- [ ] Implement `on_load` method to check configuration status
- [ ] Create helper method to check if API keys exist for providers
- [ ] Create helper method to load library stats from database
- [ ] Handle case where database doesn't exist yet (fresh install)
- [ ] Import existing config and credentials modules

#### 2.2 Dashboard State
- [ ] Create `src/plexmix/ui/states/dashboard_state.py`
- [ ] Define DashboardState inheriting from AppState
- [ ] Add state variable for recent playlists list
- [ ] Implement method to load recent playlists from database
- [ ] Add method to refresh library stats on demand
- [ ] Handle errors gracefully with try-except blocks

#### 2.3 Dashboard Page UI
- [ ] Create `src/plexmix/ui/pages/dashboard.py`
- [ ] Import DashboardState and layout component
- [ ] Create status_card component function for config status display
- [ ] Show green/red badge based on configuration status
- [ ] Include "Configure" link when not configured
- [ ] Create stats_card component function for library statistics
- [ ] Build main dashboard layout with heading
- [ ] Add configuration status section with cards for Plex, AI, Embeddings
- [ ] Add library stats section with total tracks and embedded count
- [ ] Add quick actions section with buttons for Generate and Sync
- [ ] Disable buttons when required config is missing
- [ ] Wire up button clicks to navigate to appropriate pages
- [ ] Use consistent spacing and alignment
- [ ] Update app.py to use dashboard_page with on_load callback

#### 2.4 Settings State
- [ ] Create `src/plexmix/ui/states/settings_state.py`
- [ ] Define SettingsState inheriting from AppState
- [ ] Add state variables for all Plex config fields (url, token, library)
- [ ] Add state variables for AI provider config (provider, api_key, model, temperature)
- [ ] Add state variables for embedding provider config (provider, api_key, model)
- [ ] Add state variables for advanced settings (db_path, batch_sizes, log_level)
- [ ] Add state variables for UI feedback (testing_connection, connection_status, active_tab)
- [ ] Implement method to load current settings from config module
- [ ] Implement method to save settings back to config module
- [ ] Create async test_plex_connection method with background decorator
- [ ] Attempt to connect to Plex server and return status
- [ ] Update connection_status state variable with results
- [ ] Create async test_ai_provider method
- [ ] Make test API call and return success/failure
- [ ] Create async test_embedding_provider method
- [ ] Generate test embedding and return success/failure
- [ ] Implement save_all_settings method to persist changes
- [ ] Call appropriate credential storage functions
- [ ] Update settings YAML file
- [ ] Show success toast on save

#### 2.5 Settings Page UI - Tab 1: Plex Configuration
- [ ] Create `src/plexmix/ui/pages/settings.py`
- [ ] Import SettingsState and layout component
- [ ] Create tabbed interface using Reflex tabs component
- [ ] Create first tab for Plex configuration
- [ ] Add text input for Plex server URL with label and placeholder
- [ ] Add text input for Plex username
- [ ] Add password input for Plex token
- [ ] Add help text or link explaining how to get Plex token
- [ ] Add select dropdown for music library (populated after connection)
- [ ] Add "Test Connection" button
- [ ] Wire button to call test_plex_connection method
- [ ] Show loading spinner while testing
- [ ] Display connection status with color-coded badge
- [ ] Add "Save" button to persist Plex settings

#### 2.6 Settings Page UI - Tab 2: AI Provider
- [ ] Create second tab for AI provider configuration
- [ ] Add select dropdown for provider (Gemini, OpenAI, Claude, Cohere)
- [ ] Add password input for API key
- [ ] Add select dropdown for model (options based on selected provider)
- [ ] Dynamically populate model options when provider changes
- [ ] Add slider for temperature (0.0 to 1.0)
- [ ] Display current temperature value
- [ ] Add "Test Provider" button
- [ ] Wire button to test_ai_provider method
- [ ] Show loading state during test
- [ ] Display test result with success/error message
- [ ] Add optional cost estimate calculator UI
- [ ] Add "Save" button for AI settings

#### 2.7 Settings Page UI - Tab 3: Embedding Provider
- [ ] Create third tab for embedding provider configuration
- [ ] Add select dropdown for provider (Gemini, OpenAI, Cohere, Local)
- [ ] Add password input for API key (hidden if Local selected)
- [ ] Add select dropdown for model
- [ ] Display embedding dimension as read-only text (based on model)
- [ ] Add "Test Embeddings" button
- [ ] Wire button to test_embedding_provider method
- [ ] Show loading state during test
- [ ] Display test result
- [ ] Add "Save" button

#### 2.8 Settings Page UI - Tab 4: Advanced
- [ ] Create fourth tab for advanced settings
- [ ] Add read-only text display for database path
- [ ] Add browse button to change database location (optional)
- [ ] Add read-only text display for FAISS index path
- [ ] Add number input for sync batch size
- [ ] Add number input for embedding batch size
- [ ] Add select dropdown for logging level (DEBUG, INFO, WARNING, ERROR)
- [ ] Add "Save" button

#### 2.9 Testing & Validation
- [ ] Navigate to dashboard and verify config status cards display correctly
- [ ] Navigate to settings and verify all tabs are present
- [ ] Test Plex connection with valid credentials
- [ ] Test Plex connection with invalid credentials
- [ ] Verify library dropdown populates after successful connection
- [ ] Test AI provider with valid API key
- [ ] Verify model dropdown changes when provider changes
- [ ] Test temperature slider updates value display
- [ ] Save settings and verify they persist after page reload
- [ ] Verify settings are accessible from CLI (shared config)
- [ ] Check that dashboard updates after settings are saved

**Deliverable**: Fully functional settings page and dashboard with live config status

---

## Phase 3: Library Manager

### Goal
Browse library, trigger syncs, manage tracks with real-time progress.

### TODO List

#### 3.1 Database Enhancements
- [ ] Open `src/plexmix/database/sqlite_manager.py`
- [ ] Add `get_tracks` method with pagination parameters (limit, offset)
- [ ] Add optional search parameter to filter by title/artist/album
- [ ] Add optional genre filter parameter
- [ ] Add optional year range filter parameters
- [ ] Use SQL WHERE clauses for filtering
- [ ] Add `count_tracks` method that accepts same filter parameters
- [ ] Return total count matching filters
- [ ] Add `count_tracks_with_embeddings` method
- [ ] Add `get_last_sync_time` method to retrieve last sync timestamp
- [ ] Ensure all queries use proper parameterization to prevent SQL injection
- [ ] Add indexes on commonly filtered columns (genre, year) for performance

#### 3.2 Sync Module Enhancement
- [ ] Open `src/plexmix/plex/sync.py`
- [ ] Add optional `progress_callback` parameter to main sync function
- [ ] Add optional `cancel_event` parameter for cancellation support
- [ ] Emit progress updates at regular intervals (e.g., every 10 tracks)
- [ ] Pass progress value (0.0 to 1.0) and descriptive message to callback
- [ ] Check cancel_event periodically and stop if set
- [ ] Make parameters default to None for backward compatibility
- [ ] Test that sync still works without callbacks (CLI compatibility)

#### 3.3 Library State
- [ ] Create `src/plexmix/ui/states/library_state.py`
- [ ] Define LibraryState inheriting from AppState
- [ ] Add state variables for tracks list and total count
- [ ] Add state variables for pagination (current_page, page_size)
- [ ] Add state variables for filters (search_query, genre_filter, year_min, year_max)
- [ ] Add state variables for sync status (sync_in_progress, sync_progress, sync_message, sync_error)
- [ ] Add state variable for selected tracks (for bulk operations)
- [ ] Implement `load_tracks` method to query database with current filters and pagination
- [ ] Calculate offset from current_page and page_size
- [ ] Update tracks and total_tracks state variables
- [ ] Implement `next_page` and `prev_page` methods to update current_page
- [ ] Implement `set_search_query` method with debouncing
- [ ] Use asyncio.create_task to delay execution
- [ ] Cancel pending search if new query arrives
- [ ] Implement background `start_sync` method with @rx.background decorator
- [ ] Use async with self to acquire state lock before updates
- [ ] Create async generator that yields progress updates
- [ ] Import sync function from plex module
- [ ] Pass progress callback that yields to frontend
- [ ] Handle errors and update sync_error state
- [ ] Implement `cancel_sync` method to set cancellation flag
- [ ] Implement `generate_embeddings` method for selected tracks
- [ ] Similar background task pattern as sync

#### 3.4 Track Table Component
- [ ] Create `src/plexmix/ui/components/track_table.py`
- [ ] Define track_table function that accepts tracks list
- [ ] Use Reflex table component with proper headers
- [ ] Add columns: Checkbox, Title, Artist, Album, Genre, Year, Tags, Embedding Status
- [ ] Make embedding status a badge (green if exists, gray if not)
- [ ] Add row actions: View details, Edit tags buttons
- [ ] Make columns sortable
- [ ] Add hover effects for better UX
- [ ] Support bulk selection with checkboxes
- [ ] Add "Select All" checkbox in header
- [ ] Make table responsive

#### 3.5 Library Page UI
- [ ] Create `src/plexmix/ui/pages/library.py`
- [ ] Import LibraryState and layout component
- [ ] Add page heading
- [ ] Create action bar with horizontal stack layout
- [ ] Add "Sync Library" button
- [ ] Wire to start_sync method
- [ ] Show loading spinner when sync in progress
- [ ] Disable button during sync
- [ ] Add search input box
- [ ] Wire to set_search_query method
- [ ] Add debounce so search triggers after typing stops
- [ ] Add genre filter dropdown
- [ ] Add year range inputs (min and max)
- [ ] Add "Generate Embeddings" button for selected tracks
- [ ] Disable when no tracks selected
- [ ] Display sync progress modal when sync is running
- [ ] Show progress bar with current percentage
- [ ] Display status message (e.g., "Fetching track 450/1523")
- [ ] Include cancel button that calls cancel_sync
- [ ] Auto-close modal when sync completes
- [ ] Import and use track_table component
- [ ] Pass tracks from state
- [ ] Add pagination controls below table
- [ ] Previous button (disabled on page 1)
- [ ] Page number display
- [ ] Next button (disabled on last page)
- [ ] Wire buttons to next_page and prev_page methods
- [ ] Add on_load callback to load initial tracks

#### 3.6 Testing & Validation
- [ ] Navigate to library page
- [ ] Verify tracks load and display in table
- [ ] Test pagination controls work correctly
- [ ] Search for tracks and verify results filter correctly
- [ ] Apply genre filter and verify filtering
- [ ] Apply year range filter
- [ ] Click "Sync Library" and verify sync starts
- [ ] Check progress modal appears with progress bar
- [ ] Verify progress updates in real-time
- [ ] Test cancel button stops sync
- [ ] Wait for sync to complete and verify modal closes
- [ ] Verify track count updates after sync
- [ ] Select tracks and test "Generate Embeddings" button
- [ ] Test with large library (1000+ tracks) for performance
- [ ] Verify debounced search doesn't trigger on every keystroke

**Deliverable**: Browsable, searchable library with working sync and progress tracking

---

## Phase 4: Playlist Generator

### Goal
Core feature - AI-powered mood-based playlist generation.

### TODO List

#### 4.1 Playlist Generator Module Enhancement
- [ ] Open `src/plexmix/playlist/generator.py`
- [ ] Add optional `progress_callback` parameter to generation method
- [ ] Emit progress during candidate track selection phase
- [ ] Emit progress during AI generation phase
- [ ] Make parameter default to None for backward compatibility
- [ ] Test generator still works without callback

#### 4.2 Database Playlist Support
- [ ] Open `src/plexmix/database/sqlite_manager.py`
- [ ] Create `playlists` table schema if not exists
- [ ] Columns: id, name, mood_query, track_ids (JSON), created_at, updated_at
- [ ] Add `save_playlist` method to insert new playlist
- [ ] Add `get_playlists` method to retrieve all playlists
- [ ] Add `get_playlist_by_id` method
- [ ] Add `delete_playlist` method
- [ ] Add `update_playlist` method
- [ ] Ensure track_ids are properly serialized/deserialized as JSON

#### 4.3 Generator State
- [ ] Create `src/plexmix/ui/states/generator_state.py`
- [ ] Define GeneratorState inheriting from AppState
- [ ] Add state variables for mood query input
- [ ] Add state variables for advanced options (max_tracks, genre_filters, year_range)
- [ ] Add state variable for include/exclude artists lists
- [ ] Add state variables for generation status (generating, progress, progress_message)
- [ ] Add state variable for generated playlist (list of track dicts)
- [ ] Add state variable for playlist metadata (total tracks, duration)
- [ ] Add state variable for playlist name (for saving)
- [ ] Add state variable for mood query examples list
- [ ] Implement background `generate_playlist` method with @rx.background decorator
- [ ] Use async with self for state updates
- [ ] Build filter criteria from advanced options
- [ ] Call playlist generator with progress callback
- [ ] Yield progress updates to frontend
- [ ] Store generated tracks in state
- [ ] Calculate total duration
- [ ] Handle errors gracefully
- [ ] Implement `regenerate` method to generate again with same/modified query
- [ ] Implement `save_to_plex` method
- [ ] Use PlexClient to create playlist on Plex server
- [ ] Show success/error toast
- [ ] Implement `save_locally` method
- [ ] Save playlist to database with name
- [ ] Show success toast
- [ ] Implement `export_m3u` method
- [ ] Generate M3U file content
- [ ] Trigger download in browser
- [ ] Implement `remove_track` method to remove track from generated list
- [ ] Implement `reorder_tracks` method for drag-and-drop
- [ ] Implement `use_example` method to populate query from example

#### 4.4 Generator Page UI - Input Section
- [ ] Create `src/plexmix/ui/pages/generator.py`
- [ ] Import GeneratorState and layout component
- [ ] Create two-column layout (40% input, 60% results)
- [ ] In left column, add large text area for mood query
- [ ] Add placeholder text like "Describe your mood or vibe..."
- [ ] Wire to mood_query state variable
- [ ] Create examples carousel or list
- [ ] Display 4-5 example queries ("Chill rainy day vibes", "Energetic workout", etc.)
- [ ] Make examples clickable to populate query
- [ ] Create collapsible advanced options section
- [ ] Add slider for max tracks (10-100 range)
- [ ] Add multi-select for genre filters
- [ ] Add year range inputs (min and max)
- [ ] Add text inputs for include/exclude artists
- [ ] Add large "Generate Playlist" button
- [ ] Wire to generate_playlist method
- [ ] Disable during generation
- [ ] Show loading spinner when generating

#### 4.5 Generator Page UI - Results Section
- [ ] In right column, add conditional display based on generation state
- [ ] Show loading animation when generating is true
- [ ] Include AI thinking message
- [ ] Show progress bar with current percentage
- [ ] Display progress message
- [ ] When playlist is generated, display results table
- [ ] Columns: Track number, Title, Artist, Album, Duration
- [ ] Add remove button for each track
- [ ] Implement drag-and-drop reordering if possible with Reflex
- [ ] Display playlist metadata above table
- [ ] Show total tracks count
- [ ] Show total duration (calculate from track lengths)
- [ ] Show mood query used
- [ ] Create actions section below table
- [ ] Add "Regenerate" button
- [ ] Add "Save to Plex" button with loading state
- [ ] Show input for playlist name when saving
- [ ] Add "Save Locally" button
- [ ] Add "Export M3U" button
- [ ] Wire all buttons to respective state methods
- [ ] Handle empty results case with helpful message

#### 4.6 Testing & Validation
- [ ] Navigate to generator page
- [ ] Click example query and verify it populates input
- [ ] Expand advanced options and set filters
- [ ] Enter mood query and click generate
- [ ] Verify progress modal appears
- [ ] Check progress updates in real-time
- [ ] Wait for generation to complete
- [ ] Verify playlist displays with correct tracks
- [ ] Test remove track button
- [ ] Test reordering tracks (if implemented)
- [ ] Click "Save to Plex" and verify playlist appears in Plex
- [ ] Enter playlist name and save locally
- [ ] Verify playlist saved to database
- [ ] Click "Export M3U" and verify file downloads
- [ ] Test regenerate with modified query
- [ ] Test with different AI providers
- [ ] Test with empty library (should show helpful error)
- [ ] Test with filters that match no tracks

**Deliverable**: Complete AI-powered playlist generation workflow

---

## Phase 5: AI Tagging Interface

### Goal
Batch AI tag generation and tag management.

### TODO List

#### 5.1 Tag Generator Module Enhancement
- [ ] Open `src/plexmix/ai/tag_generator.py`
- [ ] Add optional `progress_callback` parameter to batch tagging method
- [ ] Emit progress after each batch of tracks tagged
- [ ] Include batch number, total batches, and tracks tagged count
- [ ] Add optional `cancel_event` parameter for cancellation
- [ ] Check cancel event between batches
- [ ] Make parameters default to None for backward compatibility

#### 5.2 Database Tag Support
- [ ] Open `src/plexmix/database/sqlite_manager.py`
- [ ] Add method to count untagged tracks
- [ ] Add method to get tracks by filter criteria (for tag selection)
- [ ] Add method to update track tags (tags, environments, instruments)
- [ ] Add method to get recently tagged tracks (last N tracks)
- [ ] Ensure tag fields can store comma-separated or JSON arrays

#### 5.3 Tagging State
- [ ] Create `src/plexmix/ui/states/tagging_state.py`
- [ ] Define TaggingState inheriting from AppState
- [ ] Add state variables for filter criteria (genre, year_range, artist, has_no_tags)
- [ ] Add state variable for preview count (tracks matching filters)
- [ ] Add state variables for tagging status (in_progress, progress, current_batch, total_batches)
- [ ] Add state variable for tags generated count
- [ ] Add state variable for recently tagged tracks list
- [ ] Add state variable for estimated time remaining
- [ ] Implement `preview_selection` method to count matching tracks
- [ ] Query database with filter criteria
- [ ] Update preview_count state
- [ ] Implement background `start_tagging` method with @rx.background decorator
- [ ] Use async with self for state updates
- [ ] Query tracks matching filters
- [ ] Call tag generator with progress callback
- [ ] Yield progress updates to frontend
- [ ] Update tags_generated counter
- [ ] Refresh recently tagged tracks when done
- [ ] Handle errors and show error state
- [ ] Implement `cancel_tagging` method to set cancellation flag
- [ ] Implement `update_tag` method for inline editing
- [ ] Update database with new tag value
- [ ] Refresh recently tagged tracks
- [ ] Implement `tag_all_untagged` preset method
- [ ] Set filters to match untagged tracks only
- [ ] Call start_tagging

#### 5.4 Tagging Page UI - Selection Panel
- [ ] Create `src/plexmix/ui/pages/tagging.py`
- [ ] Import TaggingState and layout component
- [ ] Add page heading
- [ ] Create selection panel at top
- [ ] Add "Tag All Untagged Tracks" button as quick action
- [ ] Wire to tag_all_untagged method
- [ ] Add divider with "OR" text
- [ ] Create custom filter builder section
- [ ] Add genre multi-select dropdown
- [ ] Add year range inputs (min and max)
- [ ] Add artist search/filter input
- [ ] Add checkbox for "Has no tags"
- [ ] Add "Preview Selection" button
- [ ] Wire to preview_selection method
- [ ] Display preview count prominently
- [ ] Show count like "X tracks match filters"
- [ ] Add large "Start Tagging" button
- [ ] Disable if preview count is 0
- [ ] Wire to start_tagging method

#### 5.5 Tagging Page UI - Progress Section
- [ ] Create progress section (visible when tagging is in progress)
- [ ] Show overall progress bar
- [ ] Display current batch info (e.g., "Batch 3/10")
- [ ] Show number of tracks being processed in batch
- [ ] Display tags generated count
- [ ] Show estimated time remaining if available
- [ ] Add cancel button
- [ ] Wire to cancel_tagging method
- [ ] Hide progress section when not tagging

#### 5.6 Tagging Page UI - Recent Tags Table
- [ ] Create section below progress for recently tagged tracks
- [ ] Add heading "Recently Tagged Tracks"
- [ ] Create table with columns: Title, Artist, Tags, Environments, Instruments
- [ ] Display last 100 tagged tracks
- [ ] Add edit button for each row
- [ ] Implement inline editing mode
- [ ] Show input fields when edit is clicked
- [ ] Add save/cancel buttons for edits
- [ ] Wire save to update_tag method
- [ ] Add pagination if needed for large result sets
- [ ] Show empty state when no recently tagged tracks

#### 5.7 Testing & Validation
- [ ] Navigate to tagging page
- [ ] Click "Tag All Untagged Tracks"
- [ ] Verify preview count shows correct number
- [ ] Click "Start Tagging" and verify progress appears
- [ ] Check progress bar updates in real-time
- [ ] Verify batch info displays correctly
- [ ] Wait for tagging to complete
- [ ] Check recently tagged tracks table populates
- [ ] Test custom filter builder
- [ ] Set various filter combinations
- [ ] Preview selection and verify count changes
- [ ] Start tagging with custom filters
- [ ] Test cancel button stops tagging
- [ ] Test inline tag editing
- [ ] Edit a tag and save
- [ ] Verify tag updates in database
- [ ] Test with different AI providers
- [ ] Test with large batch (100+ tracks)

**Deliverable**: AI tagging interface with batch processing and editing

---

## Phase 6: Playlist History

### Goal
View and manage saved playlists.

### TODO List

#### 6.1 History State
- [ ] Create `src/plexmix/ui/states/history_state.py`
- [ ] Define HistoryState inheriting from AppState
- [ ] Add state variable for playlists list
- [ ] Add state variable for selected playlist details
- [ ] Add state variable for detail modal visibility
- [ ] Add state variable for sorting/filtering options
- [ ] Implement `load_playlists` method
- [ ] Query database for all saved playlists
- [ ] Sort by created date (newest first)
- [ ] Update playlists state variable
- [ ] Implement `select_playlist` method to view details
- [ ] Load full playlist details including tracks
- [ ] Update selected_playlist state
- [ ] Show detail modal
- [ ] Implement `delete_playlist` method
- [ ] Delete from database
- [ ] Show confirmation dialog before deletion
- [ ] Reload playlists after deletion
- [ ] Show success toast
- [ ] Implement `export_to_plex` method
- [ ] Similar to generator's save_to_plex
- [ ] Use playlist data to create on Plex server
- [ ] Implement `export_to_m3u` method
- [ ] Generate M3U file from playlist
- [ ] Trigger download
- [ ] Implement `close_detail_modal` method
- [ ] Add on_load callback to load playlists

#### 6.2 History Page UI - Playlist Grid
- [ ] Create `src/plexmix/ui/pages/history.py`
- [ ] Import HistoryState and layout component
- [ ] Add page heading
- [ ] Create grid layout for playlist cards
- [ ] Use responsive grid (2-4 columns based on screen width)
- [ ] For each playlist, create card component
- [ ] Display playlist name as heading
- [ ] Show mood query as subtitle
- [ ] Display track count and total duration
- [ ] Show created date
- [ ] Create thumbnail from first 4 album arts in 2x2 grid
- [ ] Or use placeholder if album art not available
- [ ] Add hover effect to show action buttons
- [ ] Include "View Details" button
- [ ] Include "Export to Plex" button
- [ ] Include "Export M3U" button
- [ ] Include "Delete" button (with warning icon)
- [ ] Wire all buttons to respective state methods
- [ ] Show empty state if no playlists saved
- [ ] Add helpful message and link to generator

#### 6.3 History Page UI - Detail Modal
- [ ] Create modal component for playlist details
- [ ] Bind visibility to detail modal state variable
- [ ] Display playlist name as modal header
- [ ] Show mood query prominently
- [ ] Display metadata (track count, duration, created date)
- [ ] Create table for full track listing
- [ ] Columns: Track number, Title, Artist, Album, Duration
- [ ] Make table scrollable if many tracks
- [ ] Add actions section in modal footer
- [ ] "Export to Plex" button
- [ ] "Export M3U" button
- [ ] "Delete Playlist" button
- [ ] "Close" button
- [ ] Wire buttons to state methods
- [ ] Show confirmation dialog before deletion

#### 6.4 Testing & Validation
- [ ] Navigate to history page
- [ ] Verify empty state shows when no playlists
- [ ] Generate and save a playlist from generator page
- [ ] Return to history and verify playlist appears
- [ ] Check card shows correct information
- [ ] Hover over card and verify action buttons appear
- [ ] Click "View Details" and verify modal opens
- [ ] Check all playlist information displays correctly
- [ ] Test "Export to Plex" from both card and modal
- [ ] Verify playlist appears in Plex
- [ ] Test "Export M3U" and verify download
- [ ] Test delete with confirmation dialog
- [ ] Verify playlist removed from grid after deletion
- [ ] Create multiple playlists and verify grid layout
- [ ] Test with playlists of varying lengths (5 tracks, 50 tracks, etc.)

**Deliverable**: Playlist history viewing and management interface

---

## Phase 7: Polish & Testing

### Goal
Refinement, optimization, comprehensive testing, and documentation.

### TODO List

#### 7.1 UI Polish - Loading States
- [ ] Review all pages for loading states
- [ ] Add skeleton screens while data loads
- [ ] Use Reflex skeleton components for tables and cards
- [ ] Add loading spinners for buttons during async operations
- [ ] Ensure all background tasks show progress or loading indicators
- [ ] Add transitions/animations for smooth UX
- [ ] Test all loading states appear correctly
- [ ] Verify loading states dismiss when operation completes

#### 7.2 UI Polish - Error Handling
- [ ] Review all async operations for error handling
- [ ] Wrap operations in try-except blocks
- [ ] Show user-friendly error messages via toast
- [ ] Log detailed errors to console for debugging
- [ ] Add error boundaries for page-level errors
- [ ] Create fallback error page with helpful message
- [ ] Test with invalid inputs (bad URLs, wrong API keys, etc.)
- [ ] Test network failures (disconnect during operation)
- [ ] Verify all errors show helpful messages, not stack traces
- [ ] Add retry buttons where appropriate

#### 7.3 UI Polish - Validation
- [ ] Add client-side validation for all form inputs
- [ ] Validate URLs match expected format
- [ ] Validate API keys meet length requirements
- [ ] Validate numeric inputs are in valid ranges
- [ ] Show inline error messages for invalid inputs
- [ ] Prevent form submission when validation fails
- [ ] Add real-time validation as user types (debounced)
- [ ] Use color coding (red borders) for invalid fields
- [ ] Show green checkmarks for valid fields
- [ ] Test all validation scenarios

#### 7.4 UI Polish - Keyboard Shortcuts
- [ ] Implement keyboard shortcuts for common actions
- [ ] Add Ctrl/Cmd+K for global search
- [ ] Add Ctrl/Cmd+N for new playlist
- [ ] Add Ctrl/Cmd+S to save settings
- [ ] Add Esc to close modals
- [ ] Add keyboard navigation for lists and tables
- [ ] Display keyboard shortcuts in help section
- [ ] Test shortcuts work on different operating systems

#### 7.5 Performance Optimization
- [ ] Profile page load times
- [ ] Optimize database queries with proper indexes
- [ ] Implement query result caching where appropriate
- [ ] Optimize component re-renders
- [ ] Use Reflex's memoization for expensive computations
- [ ] Implement virtual scrolling for very large lists (if needed)
- [ ] Test with large library (10,000+ tracks)
- [ ] Measure search response time
- [ ] Optimize image loading for playlist cards
- [ ] Use lazy loading for images
- [ ] Minify assets for production
- [ ] Test performance metrics meet success criteria

#### 7.6 Responsive Design
- [ ] Test UI on different screen sizes
- [ ] Desktop (1920x1080, 1366x768)
- [ ] Tablets (1024x768, 768x1024)
- [ ] Verify navbar collapses appropriately on smaller screens
- [ ] Check tables display properly or switch to card layout
- [ ] Ensure buttons and controls are touch-friendly
- [ ] Test modals are appropriately sized on all screens
- [ ] Fix any layout issues discovered
- [ ] Add mobile-specific optimizations if needed

#### 7.7 Unit Testing
- [ ] Create `tests/ui/test_states.py`
- [ ] Write unit tests for AppState methods
- [ ] Mock database and config calls
- [ ] Test state initialization
- [ ] Test configuration status checking
- [ ] Write tests for DashboardState
- [ ] Write tests for SettingsState
- [ ] Test connection testing methods
- [ ] Test save settings functionality
- [ ] Write tests for LibraryState
- [ ] Test pagination logic
- [ ] Test filter application
- [ ] Write tests for GeneratorState
- [ ] Test playlist generation logic
- [ ] Write tests for TaggingState
- [ ] Test filter preview and tagging
- [ ] Write tests for HistoryState
- [ ] Test playlist CRUD operations
- [ ] Ensure all tests pass
- [ ] Aim for >80% code coverage

#### 7.8 Integration Testing
- [ ] Create integration test scenarios
- [ ] Test complete user workflow: Setup → Sync → Generate → Save
- [ ] Test CLI and UI config compatibility
- [ ] Create config via CLI, verify visible in UI
- [ ] Create config via UI, verify CLI can use it
- [ ] Test with real Plex server
- [ ] Test with real AI providers (all 4: Gemini, OpenAI, Claude, Cohere)
- [ ] Test sync with various library sizes
- [ ] Test playlist generation with different queries
- [ ] Test error scenarios (no internet, wrong credentials, etc.)
- [ ] Test concurrent operations (sync while generating playlist)
- [ ] Test state persistence across page refreshes
- [ ] Document all test results

#### 7.9 Manual Testing & QA
- [ ] Create testing checklist for all features
- [ ] Recruit beta tester if possible
- [ ] Test as if you're a new user
- [ ] Can you complete setup without documentation?
- [ ] Can you generate your first playlist in under 5 minutes?
- [ ] Test all features systematically
- [ ] Try to break the UI with unexpected inputs
- [ ] Test edge cases (empty library, no API key, etc.)
- [ ] Test with slow network connection
- [ ] Verify all error messages are helpful
- [ ] Check for any visual glitches or layout issues
- [ ] Test accessibility (keyboard navigation, screen readers if possible)
- [ ] Document all bugs found

#### 7.10 Documentation
- [ ] Update main README.md with UI section
- [ ] Add installation instructions for UI extras
- [ ] Document how to launch UI (`plexmix ui`)
- [ ] Add screenshots of all main pages
- [ ] Create UI user guide section
- [ ] Explain each page's purpose and features
- [ ] Document keyboard shortcuts
- [ ] Add troubleshooting section for common issues
- [ ] Document UI-specific configuration options
- [ ] Create CHANGELOG entry for UI release
- [ ] Update pyproject.toml version
- [ ] Add UI extras to package metadata

#### 7.11 Final Review
- [ ] Review all code for consistency
- [ ] Ensure consistent naming conventions
- [ ] Check all imports are necessary
- [ ] Remove any commented-out code
- [ ] Ensure all TODOs are addressed or documented
- [ ] Review error handling across all modules
- [ ] Check all user-facing text for clarity and grammar
- [ ] Verify all links and buttons work
- [ ] Test production build
- [ ] Build with `reflex export` if applicable
- [ ] Test deployed version
- [ ] Get feedback from peer review if possible
- [ ] Address any final issues

**Deliverable**: Production-ready UI with comprehensive testing and documentation

---

## Success Criteria

### Functional Requirements
- [ ] All CLI features are accessible via UI
- [ ] Configuration changes work bidirectionally (CLI <-> UI)
- [ ] Background tasks can be monitored and cancelled
- [ ] No data loss or corruption occurs
- [ ] Error messages are clear and actionable
- [ ] All 6 main pages are fully functional

### Performance Requirements
- [ ] Page load time < 2 seconds
- [ ] Search results appear in < 500ms
- [ ] UI remains responsive during background tasks
- [ ] Handles libraries with 10,000+ tracks smoothly
- [ ] No memory leaks during extended use

### Usability Requirements
- [ ] New user can complete setup in < 10 minutes
- [ ] User can generate first playlist in < 5 minutes
- [ ] Interface is intuitive without documentation
- [ ] Works on tablets and desktops (responsive)
- [ ] Keyboard shortcuts improve power user experience

---

## Future Enhancements

These features are not in scope for the initial release but are noted for future development:

### Music Player Integration
- Embed audio preview player in UI
- Play tracks directly from track listings
- Control Plex playback from UI (if API supports)

### Analytics & Visualizations
- Visualize mood trends over time
- Track playlist generation history with charts
- Show most-used tags and genres
- Library growth over time graph

### Advanced Features
- Playlist templates (save and reuse mood queries)
- Smart playlists (auto-update based on tag rules)
- Collaborative playlists (share with other users)
- Playlist scheduling (auto-generate at intervals)
- Duplicate track detection and removal

### UI Enhancements
- Dark mode theme switcher
- Customizable color schemes
- Drag-and-drop file upload for M3U import
- Bulk track operations (batch delete, edit)
- Export to other services (Spotify, YouTube Music)

### Mobile & PWA
- Touch-optimized controls for mobile
- Progressive Web App (PWA) support for offline access
- Mobile-specific layouts
- Push notifications for completed tasks

### Multi-User Support
- User authentication and accounts
- User-specific playlists and settings
- Sharing playlists between users
- Permission levels (admin, user, guest)

---

## Getting Started

To begin implementation:

```bash
# Checkout the UI branch
git checkout feature/reflex-ui

# Install UI dependencies
poetry add reflex@^0.6.0
poetry install -E ui

# Start with Phase 1
# Follow TODO items sequentially

# Test as you go
plexmix ui

# When complete, merge to main
git checkout master
git merge feature/reflex-ui
```

Good luck with the implementation!
