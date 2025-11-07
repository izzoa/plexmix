# LLM Instructions for PlexMix

This document contains step-by-step instructions for common development tasks that LLMs should follow when working on PlexMix.

## Publishing a Release

Follow these steps when publishing a new version of PlexMix:

### 1. Increment Version Numbers

Update the version in **two** locations:

**File 1: `pyproject.toml`**
```toml
[tool.poetry]
name = "plexmix"
version = "0.2.6"  # <- Update this
```

**File 2: `src/plexmix/__init__.py`**
```python
__version__ = "0.2.6"  # <- Update this (add if doesn't exist)
```

**Version numbering:**
- Patch release (bug fixes): `0.2.5` → `0.2.6`
- Minor release (new features): `0.2.6` → `0.3.0`
- Major release (breaking changes): `0.3.0` → `1.0.0`

### 2. Stage and Commit Changes

```bash
# Stage all changes
git add -A

# Check what's staged (exclude any temporary files)
git status

# Unstage any temporary/coverage files
git restore --staged .coverage.*

# Commit with descriptive message
git commit -m "feat: Add database reset and recovery system (v0.2.6)

- Add 'plexmix db reset' command with automatic backup creation
- Add 'plexmix db info' command to show database statistics
- Implement DatabaseRecovery module for handling missing/corrupted databases
- Auto-detect and initialize empty database files
- Fix migration errors when database exists but has no tables
- Add automatic timestamped backups to ~/.plexmix/backups/
- Add safety features: confirmation prompts, statistics preview
- Update README with database management documentation
- Add command reference table comparing all database operations

Breaking Changes: None
New Commands:
  - plexmix db info
  - plexmix db reset [--force] [--no-backup]

Enhancements:
  - Automatic database recovery on missing/empty files
  - Backup system for safe reset operations
  - Clear messaging about what gets deleted vs preserved
  - Resilient to accidental database deletion"
```

**Commit Message Format:**
- Use conventional commits: `feat:`, `fix:`, `docs:`, `chore:`, etc.
- Include version in first line
- List key changes as bullet points
- Include "Breaking Changes" section if applicable
- Include "New Commands" section if applicable
- Include "Enhancements" section for improvements

### 3. Create Annotated Git Tag

```bash
git tag -a v0.2.6 -m "Release v0.2.6 - Database Reset & Recovery

New Features:
- Database reset command with automatic backups
- Database info command for statistics and health monitoring
- Automatic database recovery system
- Empty database detection and auto-initialization

Commands Added:
- plexmix db info - Show database statistics and file information
- plexmix db reset - Reset database with automatic backup

Improvements:
- Enhanced resilience to database deletion or corruption
- Automatic timestamped backups in ~/.plexmix/backups/
- Interactive confirmation prompts for safety
- Clear messaging about impacts of reset operations
- Fixed migration errors on empty database files

Documentation:
- Added Database Management section to README
- Command reference table for database operations
- Updated feature list to highlight resilience"
```

**Tag Format:**
- Always use annotated tags: `-a v0.2.6`
- Tag name must match version: `v0.2.6`
- Include comprehensive release notes in tag message
- Organize by: New Features, Commands Added, Improvements, Documentation

### 4. Push to GitHub

```bash
# Push commits
git push origin master

# Push tag
git push origin v0.2.6
```

### 5. Create GitHub Release

Use GitHub CLI to create the release:

```bash
gh release create v0.2.6 \
  --title "v0.2.6 - Database Reset & Recovery" \
  --notes "## New Features

### Database Management Commands
- **\`plexmix db info\`** - Show database statistics, file sizes, and health metrics
- **\`plexmix db reset\`** - Safely reset database with automatic backup creation

### Automatic Recovery System
- Auto-detect missing or empty database files
- Automatically initialize schema when needed
- Prevent migration errors on corrupted databases

## Enhancements

### Safety Features
- Automatic timestamped backups in \`~/.plexmix/backups/\`
- Interactive confirmation prompts before destructive operations
- Statistics preview showing what will be deleted
- Clear messaging about preserved vs deleted data

### Database Resilience
- Handles accidental database deletion gracefully
- Auto-recovery from empty database files
- Corruption detection and safe recreation

## Commands

\`\`\`bash
# View database information
plexmix db info

# Reset database (with backup)
plexmix db reset

# Reset without confirmation
plexmix db reset --force

# Reset without backup (not recommended)
plexmix db reset --no-backup
\`\`\`

## What Gets Deleted
- SQLite database (\`~/.plexmix/plexmix.db\`)
- FAISS embeddings index (\`~/.plexmix/embeddings.index\`)
- All synced metadata, tags, playlists, and embeddings

## What Gets Preserved
- Your music files on Plex server (unchanged)
- Plex server metadata (unchanged)
- PlexMix configuration (\`.env\`, \`config.yaml\`)
- API keys

## Documentation
- Added Database Management section to README
- Command reference table for all database operations
- Updated feature list to highlight resilience

## Files Changed
- \`src/plexmix/cli/main.py\` - Added db command group
- \`src/plexmix/database/recovery.py\` - New recovery module
- \`src/plexmix/database/sqlite_manager.py\` - Enhanced auto-recovery
- \`README.md\` - Complete database management docs
- \`pyproject.toml\` - Version bump to 0.2.6

## Testing
All 91 tests passing ✅"
```

**GitHub Release Notes Format:**
- Use Markdown formatting
- Include command examples with syntax highlighting
- Organize into clear sections: New Features, Enhancements, Commands, etc.
- List what gets deleted vs preserved for destructive operations
- Include file changes summary
- Add testing status

### 6. Verify Release

```bash
# Check recent commits
git log --oneline -5

# List recent tags
git tag -l | tail -5

# Verify tag was pushed
git ls-remote --tags origin

# Check GitHub release page
# Visit: https://github.com/izzoa/plexmix/releases
```

## Pre-Release Checklist

Before publishing a release, ensure:

- [ ] All tests pass: `poetry run pytest tests/ -v`
- [ ] Version incremented in `pyproject.toml`
- [ ] Version incremented in `src/plexmix/__init__.py`
- [ ] README.md updated with new features/commands
- [ ] No temporary or coverage files staged (`.coverage.*`)
- [ ] Commit message follows conventional commits format
- [ ] Tag message includes comprehensive release notes
- [ ] GitHub release notes are detailed and well-formatted

## Testing Commands

```bash
# Run all tests
poetry run pytest tests/ -v

# Run tests with coverage
poetry run pytest tests/ -v --cov=plexmix

# Run specific test file
poetry run pytest tests/test_database.py -v

# Skip slow tests
poetry run pytest tests/ -v -k "not benchmark"
```

## Building and Publishing to PyPI

**Note:** Only do this after GitHub release is published and verified.

```bash
# Build the package
poetry build

# Publish to PyPI (requires PyPI credentials)
poetry publish

# Or publish to test PyPI first
poetry publish -r testpypi
```

## Common Development Tasks

### Adding a New CLI Command

1. Edit `src/plexmix/cli/main.py`
2. Add command to appropriate typer app (app, config_app, db_app, etc.)
3. Update README.md with command documentation
4. Add tests in `tests/` directory
5. Run tests to verify

### Adding a New Feature

1. Implement feature in appropriate module
2. Add tests for the feature
3. Update README.md documentation
4. Update version numbers
5. Follow release process above

### Updating Dependencies

```bash
# Update a specific dependency
poetry update package-name

# Update all dependencies
poetry update

# Add new dependency
poetry add package-name

# Add dev dependency
poetry add --group dev package-name
```

## Git Commit Message Conventions

Use conventional commits:

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation only changes
- `style:` - Code style changes (formatting, etc.)
- `refactor:` - Code refactoring
- `perf:` - Performance improvements
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks
- `ci:` - CI/CD changes

**Example:**
```
feat: Add database reset command (v0.2.6)

- Implement db reset with automatic backups
- Add safety confirmation prompts
- Update documentation

Breaking Changes: None
```

## Important Notes

1. **Never** commit temporary files like `.coverage.*`
2. **Always** run tests before releasing
3. **Always** update README when adding new commands
4. **Never** force push to master
5. **Always** use annotated tags (`-a`) not lightweight tags
6. **Always** increment version in both `pyproject.toml` and `__init__.py`
7. Use GitHub CLI (`gh`) for creating releases to ensure consistency
8. Tag version must exactly match the version in `pyproject.toml`

## Troubleshooting

### If you forgot to push a tag:
```bash
git push origin v0.2.6
```

### If you need to delete a tag:
```bash
# Delete local tag
git tag -d v0.2.6

# Delete remote tag
git push origin :refs/tags/v0.2.6
```

### If you need to update a release:
```bash
# Delete the release (keeps the tag)
gh release delete v0.2.6

# Recreate with updated notes
gh release create v0.2.6 --title "..." --notes "..."
```

### If tests fail:
```bash
# See detailed error output
poetry run pytest tests/ -v --tb=long

# Run only failed tests
poetry run pytest tests/ -v --lf
```

## Repository Structure

```
plexmix/
├── src/plexmix/           # Main source code
│   ├── __init__.py        # Version defined here
│   ├── cli/               # CLI commands
│   ├── database/          # Database modules
│   ├── plex/              # Plex integration
│   └── ai/                # AI providers
├── tests/                 # Test suite
├── README.md              # User documentation
├── pyproject.toml         # Version and dependencies
├── LLM.md                 # This file
└── AGENTS.md              # Development notes
```
