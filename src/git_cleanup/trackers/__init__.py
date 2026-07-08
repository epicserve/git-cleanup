"""Issue tracker provider registry."""

from __future__ import annotations

from rich.console import Console

from git_cleanup.config import Config
from git_cleanup.trackers.base import IssueTracker

_console = Console(stderr=True)


def _build_jira(config: Config) -> IssueTracker | None:
    from git_cleanup.trackers.jira import JiraTracker

    if not (config.jira_url and config.jira_email and config.jira_api_token):
        _console.print(
            "[yellow]⚠ Jira is not configured (need JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN "
            "or ~/.config/git-cleanup/config.toml); continuing with git-only info[/yellow]"
        )
        return None
    return JiraTracker(
        url=config.jira_url,
        email=config.jira_email,
        api_token=config.jira_api_token,
        done_statuses=config.done_statuses,
    )


_PROVIDERS = {
    "jira": _build_jira,
}


def get_tracker(config: Config) -> IssueTracker | None:
    if config.provider in ("", "none"):
        return None
    builder = _PROVIDERS.get(config.provider)
    if builder is None:
        _console.print(
            f"[yellow]⚠ Unknown tracker provider {config.provider!r}; "
            "continuing with git-only info[/yellow]"
        )
        return None
    return builder(config)
