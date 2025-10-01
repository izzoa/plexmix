# PlexMix

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

AI-powered Plex playlist generator using mood-based queries

## Features

- Sync your Plex music library to a local database
- Generate embeddings for semantic music search
- Create mood-based playlists using AI (Google Gemini, OpenAI GPT, Anthropic Claude)
- Search your music library with natural language
- Manage playlists directly in Plex

## Quick Start

Only requires a Google API key to get started!

```bash
# Install
pip install plexmix

# Run setup wizard
plexmix config init

# Sync your library
plexmix sync full

# Create a playlist
plexmix create "upbeat morning energy"
```

## Installation

```bash
pip install plexmix
```

Or with Poetry:

```bash
poetry install
```

## Configuration

PlexMix uses Google Gemini by default for both AI playlist generation and embeddings, requiring only a single API key.

### Required
- Plex server URL and token
- Google API key (for Gemini AI and embeddings)

### Optional
- OpenAI API key (alternative embeddings/AI)
- Anthropic API key (alternative AI)

## License

MIT License - see LICENSE file for details
