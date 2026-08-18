# List available recipes
default:
    @just --list

# Format the justfile and Python code
format: format_just format_python

# Format the justfile
format_just:
    just --fmt --unstable

# Sort imports and format Python code
format_python:
    uv run ruff check --select I --fix
    uv run ruff format

# Lint Python code and check formatting
lint:
    uv run ruff check
    uv run ruff format --check

# Run the test suite
test *FLAGS:
    uv run pytest {{ FLAGS }}

# Run formatting, linting, and tests
pre_commit: format lint test

# Bump the version (bump can be 'major', 'minor', or 'patch'), commit, and tag
version_bump bump:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "$(git status --porcelain -- ':!CHANGELOG.md')" ]; then
        echo "Error: working tree has changes beyond CHANGELOG.md. Commit or stash them first." >&2
        exit 1
    fi
    OLD_VERSION=$(uv version --short)
    uv version --bump {{ bump }}
    NEW_VERSION=$(uv version --short)
    git add pyproject.toml uv.lock CHANGELOG.md
    git commit -m "Bump version to v${NEW_VERSION}"
    git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"
    echo "Bumped v${OLD_VERSION} → v${NEW_VERSION}."
    echo "Now run: git push origin main v${NEW_VERSION}"
