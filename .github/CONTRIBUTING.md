# Contributing to PlexMix

Thank you for your interest in contributing to PlexMix!

## Development Setup

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/yourusername/plexmix.git
   cd plexmix
   ```

3. Install dependencies:
   ```bash
   poetry install
   ```

4. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Code Quality

Before submitting a pull request, ensure your code passes all checks:

```bash
poetry run pytest
poetry run ruff check src/
poetry run mypy src/
poetry run black src/
```

## Testing

- Write tests for new features
- Maintain or improve code coverage
- Run the full test suite before submitting

## Pull Request Process

1. Update the README.md with details of changes if applicable
2. Update the version in `pyproject.toml` following [Semantic Versioning](https://semver.org/)
3. The PR will be merged once you have approval from maintainers

## Code Style

- Follow PEP 8 guidelines
- Use type hints for all functions
- Write docstrings for public APIs
- Keep lines under 100 characters
- Use meaningful variable names

## Commit Messages

- Use clear, descriptive commit messages
- Start with a verb in present tense (e.g., "Add feature", "Fix bug")
- Reference issue numbers when applicable

## Questions?

Feel free to open an issue for discussion before starting work on major changes.
