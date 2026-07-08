from pathlib import Path

from git_cleanup.config import DEFAULT_PROTECTED, default_config_path, load_config


def test_defaults_when_no_file(tmp_path: Path):
    config = load_config(tmp_path / "missing.toml", env={})
    assert config.provider == "jira"
    assert config.jira_url is None
    assert config.protected_branches == DEFAULT_PROTECTED
    assert config.archive_age_days == 90


def test_file_values(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[tracker]
provider = "jira"

[jira]
url = "https://acme.atlassian.net"
email = "me@acme.com"
api_token = "secret"

[cleanup]
protected_branches = ["main", "release"]
done_statuses = ["Won't Do"]
archive_age_days = 30
"""
    )
    config = load_config(path, env={})
    assert config.jira_url == "https://acme.atlassian.net"
    assert config.jira_email == "me@acme.com"
    assert config.jira_api_token == "secret"
    assert config.protected_branches == frozenset({"main", "release"})
    assert config.done_statuses == frozenset({"won't do"})
    assert config.archive_age_days == 30


def test_env_overrides_file(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[jira]\nurl = "https://file.example"\nemail = "file@x.com"\n')
    env = {"JIRA_URL": "https://env.example", "JIRA_API_TOKEN": "envtoken"}
    config = load_config(path, env=env)
    assert config.jira_url == "https://env.example"
    assert config.jira_email == "file@x.com"  # not overridden
    assert config.jira_api_token == "envtoken"


def test_xdg_config_home(tmp_path: Path):
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    assert default_config_path(env) == tmp_path / "git-cleanup" / "config.toml"


def test_provider_none(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[tracker]\nprovider = "none"\n')
    assert load_config(path, env={}).provider == "none"
