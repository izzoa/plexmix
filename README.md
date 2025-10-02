# PlexMix

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**AI-powered Plex playlist generator using mood-based queries**

PlexMix syncs your Plex music library to a local SQLite database, generates semantic embeddings for tracks, and uses AI to create personalized playlists based on mood descriptions.

## Features

- ✨ **Simple Setup** - Only requires a Google API key to get started
- 🎵 **Smart Sync** - Syncs Plex music library with incremental updates
- 🤖 **AI-Powered** - Uses Google Gemini, OpenAI GPT, or Anthropic Claude
- 🏷️ **AI Tagging** - Automatically generates tags, environments, and instruments for tracks
- 🔍 **Semantic Search** - FAISS vector similarity search for intelligent track matching
- 🎨 **Mood-Based** - Generate playlists from natural language descriptions
- ⚡ **Fast** - Local database with optimized indexes and full-text search
- 🎯 **Flexible** - Filter by genre, year, rating, artist, environment, and instrument

## Quick Start

```bash
# Install dependencies
poetry install

# Run setup wizard
poetry run plexmix config init

# Sync your Plex library (generates embeddings automatically)
poetry run plexmix sync full

# Generate AI tags for tracks (enhances search quality)
poetry run plexmix tags generate

# Create a playlist
poetry run plexmix create "upbeat morning energy"

# With filters
poetry run plexmix create "chill evening vibes" --genre jazz --year-min 2010 --limit 30

# Filter by environment and instrument
poetry run plexmix create "focus music" --environment study --instrument piano

# Use alternative AI provider
poetry run plexmix create "workout motivation" --provider openai

# If you encounter issues (e.g., "0 candidate tracks")
poetry run plexmix doctor
```

## Installation

### From Source (Recommended)

```bash
git clone https://github.com/izzoa/plexmix.git
cd plexmix
poetry install
```

### From PyPI (Coming Soon)

```bash
pip install plexmix
```

## Configuration

PlexMix uses **Google Gemini by default** for both AI playlist generation and embeddings, requiring only a **single API key**!

### Required

- **Plex Server**: URL and authentication token
- **Google API Key**: For Gemini AI and embeddings ([Get one here](https://makersuite.google.com/app/apikey))

### Optional Alternative Providers

- **OpenAI API Key**: For GPT models and text-embedding-3-small
- **Anthropic API Key**: For Claude models
- **Local Embeddings**: sentence-transformers (free, offline, no API key needed)

### Getting a Plex Token

1. Open Plex Web App
2. Play any media item
3. Click the three dots (...) → Get Info
4. View XML
5. Copy the `X-Plex-Token` from the URL

## Usage

### Configuration Commands

```bash
# Interactive setup wizard
plexmix config init

# Show current configuration
plexmix config show
```

### Sync Commands

```bash
# Full sync with embedding generation
plexmix sync full

# Sync without embeddings
plexmix sync full --no-embeddings
```

### Database Health Check

```bash
# Diagnose and fix database issues
plexmix doctor
```

**What does doctor do?**
- Detects orphaned embeddings (embeddings that reference deleted tracks)
- Shows database health status (track count, embeddings, orphans)
- Interactively removes orphaned data
- Regenerates missing embeddings
- Rebuilds vector index

**When to use:**
- After "No tracks found matching criteria" errors
- When playlist generation finds 0 candidates
- After database corruption or manual track deletion
- Periodic maintenance to keep database healthy

### Tag Generation

```bash
# Generate AI tags for all untagged tracks
plexmix tags generate

# Use alternative AI provider
plexmix tags generate --provider openai

# Skip embedding regeneration (faster, but tags won't be in search)
plexmix tags generate --no-regenerate-embeddings
```

**What are tags?**
AI-generated metadata (per track) that enhances semantic search:
- **Tags** (3-5): Mood descriptors like energetic, melancholic, upbeat, chill, intense
- **Environments** (1-3): Best-fit contexts like work, study, focus, relax, party, workout, sleep, driving, social
- **Instruments** (1-3): Most prominent instruments like piano, guitar, saxophone, drums, bass, synth, vocals, strings

All metadata is automatically included in embeddings for more accurate mood-based playlist generation.

### Playlist Generation

```bash
# Basic playlist (prompts for track count)
plexmix create "happy upbeat summer vibes"

# Specify track count
plexmix create "rainy day melancholy" --limit 25

# Filter by genre
plexmix create "energetic workout" --genre rock --limit 40

# Filter by year range
plexmix create "90s nostalgia" --year-min 1990 --year-max 1999

# Filter by environment (work, study, focus, relax, party, workout, sleep, driving, social)
plexmix create "workout energy" --environment workout

# Filter by instrument (piano, guitar, saxophone, drums, etc.)
plexmix create "piano jazz" --instrument piano

# Use specific AI provider
plexmix create "chill study session" --provider claude

# Custom playlist name
plexmix create "morning coffee" --name "Perfect Morning Mix"

# Don't create in Plex (save locally only)
plexmix create "test playlist" --no-create-in-plex
```

## Architecture

PlexMix uses a two-stage retrieval system with AI-enhanced tagging:

1. **AI Tagging** → Tracks receive:
   - 3-5 descriptive tags (mood, energy, tempo, emotion)
   - 1-3 environments (work, study, focus, relax, party, workout, sleep, driving, social)
   - 1-3 instruments (piano, guitar, saxophone, drums, bass, synth, vocals, strings, etc.)
2. **SQL Filters** → Filter tracks by genre, year, rating, artist, environment, instrument
3. **FAISS Similarity Search** → Retrieve top-K candidates using semantic embeddings (includes all metadata)
4. **LLM Selection** → AI provider selects final tracks matching the mood

### Technology Stack

- **Language**: Python 3.10+
- **CLI**: Typer with Rich console output
- **Database**: SQLite with FTS5 full-text search
- **Vector Search**: FAISS (CPU) with cosine similarity
- **AI Providers**: Google Gemini (default), OpenAI GPT, Anthropic Claude
- **Embeddings**: Google Gemini (3072d), OpenAI (1536d), Local (384-768d)
- **Plex Integration**: PlexAPI

### Project Structure

```
plexmix/
├── src/plexmix/
│   ├── ai/               # AI provider implementations
│   │   ├── base.py       # Abstract base class
│   │   ├── gemini_provider.py
│   │   ├── openai_provider.py
│   │   ├── claude_provider.py
│   │   └── tag_generator.py  # AI-based tag generation
│   ├── cli/              # Command-line interface
│   │   └── main.py       # Typer CLI app
│   ├── config/           # Configuration management
│   │   ├── settings.py   # Pydantic settings
│   │   └── credentials.py # Keyring integration
│   ├── database/         # Database layer
│   │   ├── models.py     # Pydantic models
│   │   ├── sqlite_manager.py # SQLite CRUD
│   │   └── vector_index.py   # FAISS index
│   ├── plex/             # Plex integration
│   │   ├── client.py     # PlexAPI wrapper
│   │   └── sync.py       # Sync engine
│   ├── playlist/         # Playlist generation
│   │   └── generator.py  # Core generation logic
│   └── utils/            # Utilities
│       ├── embeddings.py # Embedding providers
│       └── logging.py    # Logging setup
└── tests/                # Test suite
```

## Database Schema

PlexMix stores all music metadata locally:

- **artists**: Artist information
- **albums**: Album details with artist relationships
- **tracks**: Track metadata with full-text search, AI-generated tags (3-5), environments (1-3), and instruments (1-3)
- **embeddings**: Vector embeddings for semantic search (includes all AI-generated metadata)
- **playlists**: Generated playlist metadata
- **sync_history**: Synchronization audit log

## Embedding Providers

| Provider | Model | Dimensions | API Key Required |
|----------|-------|------------|------------------|
| **Google Gemini** (default) | gemini-embedding-001 | 3072 | Yes |
| OpenAI | text-embedding-3-small | 1536 | Yes |
| Local | all-MiniLM-L6-v2 | 384 | No |

## AI Providers

| Provider | Model | Context | Notes |
|----------|-------|---------|-------|
| **Google Gemini** (default) | gemini-2.5-flash | ~1M tokens | Fast, accurate, cost-effective |
| OpenAI | gpt-5-mini | ~400K tokens | Latest model, high quality |
| OpenAI | gpt-5-nano | ~400K tokens | Fastest, most efficient |
| OpenAI | gpt-4o-mini | ~128K tokens | Previous generation |
| Anthropic | claude-sonnet-4-5 | ~200K tokens | Latest model, excellent reasoning |
| Anthropic | claude-3-5-haiku-20241022 | ~200K tokens | Fast, efficient |

## Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/izzoa/plexmix.git
cd plexmix

# Install with development dependencies
poetry install

# Run tests
poetry run pytest

# Format code
poetry run black src/

# Lint
poetry run ruff src/

# Type check
poetry run mypy src/
```

### Running Tests

```bash
poetry run pytest
poetry run pytest --cov=plexmix --cov-report=html
```

## Troubleshooting

### "No music libraries found"
- Ensure your Plex server has a music library
- Verify your Plex token is correct
- Check server URL is accessible

### "Failed to generate embeddings"
- Verify API keys are configured correctly
- Check internet connection
- Try local embeddings: `--embedding-provider local`

### "No tracks found matching criteria"
- **First, try:** `plexmix doctor` to check for database issues
- Ensure library is synced: `plexmix sync full`
- Check filters aren't too restrictive
- Verify embeddings were generated

### "0 candidate tracks" or "No orphaned embeddings"
- This usually means embeddings reference old track IDs
- **Solution:** Run `plexmix doctor` to detect and fix orphaned embeddings
- The doctor will clean up orphaned data and regenerate embeddings

### Performance Tips

- Use local embeddings for faster offline operation
- Run sync during off-peak hours for large libraries
- Adjust candidate pool size based on library size
- Use filters to narrow search space

## Roadmap

- [ ] Docker support
- [ ] Web UI dashboard
- [ ] Multi-library support
- [ ] Playlist templates
- [ ] Smart shuffle and ordering
- [ ] Export/import playlists (M3U, JSON)
- [ ] Audio feature analysis integration

## Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) for details

## Acknowledgments

- Built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/)
- Plex integration via [python-plexapi](https://github.com/pkkid/python-plexapi)
- Vector search powered by [FAISS](https://github.com/facebookresearch/faiss)
- AI providers: Google, OpenAI, Anthropic

---

**Made with ❤️ for music lovers**
