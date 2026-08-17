# Contributing

## Development setup

Use Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test,streamlit]"
python -m pytest
```

Build the distribution before submitting release-related changes:

```bash
python -m pip wheel --no-deps --wheel-dir dist .
```

## Pull requests

- Keep the dependency-free core separate from framework adapters.
- Add tests for every behavior change.
- Never log or commit authentication tokens or credentials.
- Update `CHANGELOG.md` for user-visible changes.
- Keep public APIs typed and backward-compatible within a minor release.
