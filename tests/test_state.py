import json
from pathlib import Path

from git_cleanup.state import default_state_path, load_repo_state, save_repo_state

REPO = Path("/some/repo")


def test_missing_file_is_empty(tmp_path: Path):
    assert load_repo_state(REPO, path=tmp_path / "missing.json") == {}


def test_corrupt_file_is_empty(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert load_repo_state(REPO, path=path) == {}


def test_non_dict_entry_is_empty(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({str(REPO): "junk"}))
    assert load_repo_state(REPO, path=path) == {}


def test_save_load_roundtrip(tmp_path: Path):
    path = tmp_path / "state.json"
    save_repo_state(REPO, {"filter": "mine", "sort": "-age"}, path=path)
    assert load_repo_state(REPO, path=path) == {"filter": "mine", "sort": "-age"}


def test_save_preserves_other_repos(tmp_path: Path):
    path = tmp_path / "state.json"
    other = Path("/other/repo")
    save_repo_state(other, {"filter": "merged"}, path=path)
    save_repo_state(REPO, {"filter": "mine"}, path=path)
    assert load_repo_state(other, path=path) == {"filter": "merged"}


def test_save_creates_parent_dirs(tmp_path: Path):
    path = tmp_path / "deep" / "nested" / "state.json"
    save_repo_state(REPO, {"sort": "branch"}, path=path)
    assert load_repo_state(REPO, path=path) == {"sort": "branch"}


def test_save_replaces_previous_view(tmp_path: Path):
    path = tmp_path / "state.json"
    save_repo_state(REPO, {"filter": "mine", "sort": "-age"}, path=path)
    save_repo_state(REPO, {"filter": "", "sort": "branch"}, path=path)
    assert load_repo_state(REPO, path=path) == {"filter": "", "sort": "branch"}


def test_xdg_state_home(tmp_path: Path):
    env = {"XDG_STATE_HOME": str(tmp_path)}
    assert default_state_path(env) == tmp_path / "git-cleanup" / "state.json"


def test_default_state_path_fallback():
    path = default_state_path(env={})
    assert path == Path.home() / ".local" / "state" / "git-cleanup" / "state.json"
