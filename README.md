# PlexMix

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**AI-powered Plex playlist generator using mood-based queries**

PlexMix syncs your Plex music library to a local SQLite database, generates semantic embeddings for tracks, and uses AI to create personalized playlists based on mood descriptions.

## Features

✨ **Simple Setup** - Only requires a Google API key to get started
🎵 **Smart Sync** - Syncs Plex music library with incremental updates
🤖 **AI-Powered** - Uses Google Gemini, OpenAI GPT, or Anthropic Claude
🔍 **Semantic Search** - FAISS vector similarity search for intelligent track matching
🎨 **Mood-Based** - Generate playlists from natural language descriptions
⚡ **Fast** - Local database with optimized indexes and full-text search
🎯 **Flexible** - Filter by genre, year, rating, and artist

## Quick Start

```bash
# Install dependencies
poetry install

# Run setup wizard
poetry run plexmix config init

# Sync your Plex library (generates embeddings automatically)
poetry run plexmix sync full

# Create a playlist
poetry run plexmix create "upbeat morning energy"

# With filters
poetry run plexmix create "chill evening vibes" --genre jazz --year-min 2010 --limit 30

# Use alternative AI provider
poetry run plexmix create "workout motivation" --provider openai
```

## Installation

### From Source (Recommended)

```bash
git clone https://github.com/yourusername/plexmix.git
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

# Use specific AI provider
plexmix create "chill study session" --provider claude

# Custom playlist name
plexmix create "morning coffee" --name "Perfect Morning Mix"

# Don't create in Plex (save locally only)
plexmix create "test playlist" --no-create-in-plex
```

## Architecture

PlexMix uses a two-stage retrieval system:

1. **SQL Filters** → Filter tracks by genre, year, rating, artist
2. **FAISS Similarity Search** → Retrieve top-K candidates using semantic embeddings
3. **LLM Selection** → AI provider selects final tracks matching the mood

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
│   │   └── claude_provider.py
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
- **tracks**: Track metadata with full-text search
- **embeddings**: Vector embeddings for semantic search
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
| **Google Gemini** (default) | gemini-2.0-flash-exp | ~1M tokens | Fast, accurate, cost-effective |
| OpenAI | gpt-4o-mini | ~128K tokens | High quality, moderate cost |
| Anthropic | claude-3-5-sonnet | ~200K tokens | Excellent reasoning |

## Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/yourusername/plexmix.git
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
- Ensure library is synced: `plexmix sync full`
- Check filters aren't too restrictive
- Verify embeddings were generated

### Performance Tips

- Use local embeddings for faster offline operation
- Run sync during off-peak hours for large libraries
- Adjust candidate pool size based on library size
- Use filters to narrow search space

## Roadmap

- [ ] Incremental sync support
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
