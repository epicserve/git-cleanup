import httpx
import respx

from git_cleanup.models import IssueState
from git_cleanup.trackers.jira import CHUNK_SIZE, JiraTracker

BASE = "https://acme.atlassian.net"


def make_tracker(**kwargs) -> JiraTracker:
    return JiraTracker(url=BASE, email="me@acme.com", api_token="tok", **kwargs)


def jira_issue(key: str, status: str = "Done", category: str = "done") -> dict:
    return {
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "status": {"name": status, "statusCategory": {"key": category}},
        },
    }


def test_extract_key():
    tracker = make_tracker()
    assert tracker.extract_key("abc-123-fix-login") == "ABC-123"
    assert tracker.extract_key("feature/ABC-99_stuff") == "ABC-99"
    assert tracker.extract_key("no-issue-here") is None
    assert tracker.extract_key("main") is None
    assert tracker.extract_key("123-nope") is None
    # timestamp-like suffixes are not issue keys
    assert tracker.extract_key("add-claude-github-actions-1768319907126") is None


@respx.mock
def test_batch_fetch_single_request():
    route = respx.post(f"{BASE}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={
                "issues": [jira_issue("ABC-1"), jira_issue("ABC-2", "In Review", "indeterminate")]
            },
        )
    )
    result = make_tracker().fetch_issues(["ABC-1", "ABC-2", "ABC-1"])  # dupe collapses
    assert route.call_count == 1
    assert result["ABC-1"].state is IssueState.DONE
    assert result["ABC-2"].state is IssueState.OPEN
    assert result["ABC-2"].status == "In Review"
    assert result["ABC-1"].url == f"{BASE}/browse/ABC-1"


@respx.mock
def test_chunking_over_50_keys():
    route = respx.post(f"{BASE}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": []})
    )
    keys = [f"ABC-{i}" for i in range(CHUNK_SIZE + 10)]
    make_tracker().fetch_issues(keys)
    assert route.call_count == 2


@respx.mock
def test_400_falls_back_to_per_key():
    respx.post(f"{BASE}/rest/api/3/search/jql").mock(return_value=httpx.Response(400))
    respx.get(f"{BASE}/rest/api/3/issue/ABC-1").mock(
        return_value=httpx.Response(200, json=jira_issue("ABC-1"))
    )
    respx.get(f"{BASE}/rest/api/3/issue/GONE-9").mock(return_value=httpx.Response(404))
    result = make_tracker().fetch_issues(["ABC-1", "GONE-9"])
    assert set(result) == {"ABC-1"}


@respx.mock
def test_auth_failure_degrades_to_empty():
    respx.post(f"{BASE}/rest/api/3/search/jql").mock(return_value=httpx.Response(401))
    assert make_tracker().fetch_issues(["ABC-1"]) == {}


@respx.mock
def test_network_error_degrades_to_empty():
    respx.post(f"{BASE}/rest/api/3/search/jql").mock(side_effect=httpx.ConnectError)
    assert make_tracker().fetch_issues(["ABC-1"]) == {}


@respx.mock
def test_done_statuses_override():
    respx.post(f"{BASE}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200, json={"issues": [jira_issue("ABC-1", "Won't Do", "indeterminate")]}
        )
    )
    tracker = make_tracker(done_statuses=frozenset({"won't do"}))
    assert tracker.fetch_issues(["ABC-1"])["ABC-1"].state is IssueState.DONE
