"""Configuration loading: TOML file with environment-variable overrides."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROTECTED = frozenset({"main", "master", "develop"})
DEFAULT_ARCHIVE_AGE_DAYS = 90


@dataclass(frozen=True)
class Config:
    provider: str = "jira"
    jira_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    protected_branches: frozenset[str] = field(default_factory=lambda: DEFAULT_PROTECTED)
    done_statuses: frozenset[str] = frozenset()
    archive_age_days: int = DEFAULT_ARCHIVE_AGE_DAYS


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "git-cleanup" / "config.toml"


def _repo_overrides(data: dict, repo_root: Path | None) -> dict:
    """Sections from a [repos."<path>"] table matching repo_root, or {}."""
    if repo_root is None:
        return {}
    resolved = repo_root.expanduser().resolve()
    for key, sections in data.get("repos", {}).items():
        if Path(key).expanduser().resolve() == resolved:
            return sections
    return {}


def load_config(
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> Config:
    """Load config with precedence: env var > repo override > config file > default."""
    env = os.environ if env is None else env
    if path is None:
        path = default_config_path(env)

    data: dict = {}
    if path.is_file():
        with path.open("rb") as f:
            data = tomllib.load(f)

    overrides = _repo_overrides(data, repo_root)

    def section(name: str) -> dict:
        return {**data.get(name, {}), **overrides.get(name, {})}

    tracker = section("tracker")
    jira = section("jira")
    cleanup = section("cleanup")

    return Config(
        provider=(tracker.get("provider") or "jira").strip().lower(),
        jira_url=env.get("JIRA_URL") or jira.get("url"),
        jira_email=env.get("JIRA_EMAIL") or jira.get("email"),
        jira_api_token=env.get("JIRA_API_TOKEN") or jira.get("api_token"),
        protected_branches=frozenset(cleanup.get("protected_branches", DEFAULT_PROTECTED)),
        done_statuses=frozenset(s.lower() for s in cleanup.get("done_statuses", [])),
        archive_age_days=int(cleanup.get("archive_age_days", DEFAULT_ARCHIVE_AGE_DAYS)),
    )
