"""Jira issue tracker provider (Jira Cloud REST API v3, API-token auth)."""

from __future__ import annotations

import re
from collections.abc import Sequence

import httpx
from rich.console import Console

from git_cleanup.models import IssueInfo, IssueState

# project key 2-10 chars, issue number at most 6 digits — rejects
# timestamp-like suffixes such as "github-actions-1768319907126"
JIRA_KEY_RE = re.compile(r"(?i)(?<![a-z0-9])([a-z][a-z0-9]{1,9}-\d{1,6})(?!\d)")
CHUNK_SIZE = 50

_console = Console(stderr=True)


class JiraTracker:
    def __init__(
        self,
        url: str,
        email: str,
        api_token: str,
        done_statuses: frozenset[str] = frozenset(),
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = url.rstrip("/")
        self.done_statuses = done_statuses
        self._client = client or httpx.Client(
            base_url=self.base_url,
            auth=(email, api_token),
            timeout=15.0,
            headers={"Accept": "application/json"},
        )

    def extract_key(self, branch_name: str) -> str | None:
        match = JIRA_KEY_RE.search(branch_name)
        return match.group(1).upper() if match else None

    def fetch_issues(self, keys: Sequence[str]) -> dict[str, IssueInfo]:
        unique = sorted(set(keys))
        results: dict[str, IssueInfo] = {}
        try:
            for start in range(0, len(unique), CHUNK_SIZE):
                chunk = unique[start : start + CHUNK_SIZE]
                results.update(self._fetch_chunk(chunk))
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            detail = str(exc) or exc.__class__.__name__
            _console.print(
                f"[yellow]⚠ Jira unavailable, continuing with git-only info: "
                f"{detail}[/yellow]"
            )
            return {}
        return results

    def _fetch_chunk(self, keys: Sequence[str]) -> dict[str, IssueInfo]:
        jql = f"key in ({', '.join(keys)})"
        response = self._client.post(
            "/rest/api/3/search/jql",
            json={"jql": jql, "fields": ["status", "summary"], "maxResults": len(keys)},
        )
        if response.status_code == 400:
            # Jira rejects the whole "key in (...)" query if any key is unknown;
            # retry the chunk one key at a time.
            return self._fetch_individually(keys)
        if response.status_code in (401, 403):
            raise httpx.HTTPStatusError(
                f"Jira authentication failed ({response.status_code})",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        issues = response.json().get("issues", [])
        return {issue["key"]: self._to_info(issue) for issue in issues}

    def _fetch_individually(self, keys: Sequence[str]) -> dict[str, IssueInfo]:
        results: dict[str, IssueInfo] = {}
        for key in keys:
            response = self._client.get(
                f"/rest/api/3/issue/{key}",
                params={"fields": "status,summary"},
            )
            if response.status_code == 404:
                continue
            if response.status_code in (401, 403):
                raise httpx.HTTPStatusError(
                    f"Jira authentication failed ({response.status_code})",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            issue = response.json()
            results[issue["key"]] = self._to_info(issue)
        return results

    def _to_info(self, issue: dict) -> IssueInfo:
        fields = issue.get("fields", {})
        status = fields.get("status") or {}
        status_name = status.get("name", "Unknown")
        category = (status.get("statusCategory") or {}).get("key", "")
        if category == "done" or status_name.lower() in self.done_statuses:
            state = IssueState.DONE
        elif category:
            state = IssueState.OPEN
        else:
            state = IssueState.UNKNOWN
        return IssueInfo(
            key=issue["key"],
            summary=fields.get("summary", ""),
            status=status_name,
            state=state,
            url=f"{self.base_url}/browse/{issue['key']}",
        )
