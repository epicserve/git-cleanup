"""All git subprocess interactions.

Every read is a constant number of git calls regardless of branch count.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawRef:
    refname: str  # e.g. refs/heads/foo or refs/remotes/origin/foo
    sha: str
    author_email: str
    author_name: str
    committed_at: datetime
    ahead: int | None = None  # vs upstream; None when no upstream (or remote ref)
    behind: int | None = None
    upstream_gone: bool = False

    @property
    def is_remote(self) -> bool:
        return self.refname.startswith("refs/remotes/")

    @property
    def short_name(self) -> str:
        if self.is_remote:
            return self.refname.removeprefix("refs/remotes/origin/")
        return self.refname.removeprefix("refs/heads/")


def run_git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def in_git_repo(cwd: Path | None = None) -> bool:
    try:
        return run_git("rev-parse", "--is-inside-work-tree", cwd=cwd) == "true"
    except GitError:
        return False


def repo_root(cwd: Path | None = None) -> Path:
    """Absolute path of the working-tree top level."""
    return Path(run_git("rev-parse", "--show-toplevel", cwd=cwd))


def has_origin(cwd: Path | None = None) -> bool:
    try:
        remotes = run_git("remote", cwd=cwd).splitlines()
    except GitError:
        return False
    return "origin" in remotes


def fetch_prune(cwd: Path | None = None) -> None:
    run_git("fetch", "--prune", "origin", cwd=cwd)


_SSH_URL_RE = re.compile(r"^(?:ssh://)?git@(?P<host>[^:/]+)[:/](?P<path>.+)$")


def origin_web_url(cwd: Path | None = None) -> str | None:
    """https:// project page derived from origin's URL.

    Handles https and ssh remote forms; returns None for anything else
    (e.g. a filesystem path), meaning no browser links are available.
    """
    try:
        raw = run_git("remote", "get-url", "origin", cwd=cwd)
    except GitError:
        return None
    raw = raw.removesuffix(".git").rstrip("/")
    if raw.startswith(("https://", "http://")):
        return raw
    match = _SSH_URL_RE.match(raw)
    if match:
        return f"https://{match['host']}/{match['path']}"
    return None


def compare_url(web_url: str, base: str, branch: str) -> str:
    """GitHub-style three-dot compare: what `branch` adds since diverging from `base`."""
    return f"{web_url}/compare/{quote(base, safe='/')}...{quote(branch, safe='/')}"


def get_default_branch(cwd: Path | None = None) -> str:
    try:
        ref = run_git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=cwd)
        return ref.removeprefix("origin/")
    except GitError:
        pass
    try:
        out = run_git("ls-remote", "--symref", "origin", "HEAD", cwd=cwd)
        for line in out.splitlines():
            if line.startswith("ref:"):
                # "ref: refs/heads/main\tHEAD"
                return line.split()[1].removeprefix("refs/heads/")
    except GitError:
        pass
    for candidate in ("main", "master"):
        try:
            run_git("show-ref", "--verify", f"refs/remotes/origin/{candidate}", cwd=cwd)
            return candidate
        except GitError:
            continue
    raise GitError("could not determine the default branch of origin")


def get_current_branch(cwd: Path | None = None) -> str | None:
    return run_git("branch", "--show-current", cwd=cwd) or None


def get_user_email(cwd: Path | None = None) -> str:
    try:
        return run_git("config", "user.email", cwd=cwd)
    except GitError:
        return ""


_REF_FORMAT = (
    "%(refname)%09%(objectname)%09%(authoremail:trim)%09%(authorname)"
    "%09%(committerdate:iso8601-strict)%09%(upstream)%09%(upstream:track)"
)


def _parse_track(upstream: str, track: str) -> tuple[int | None, int | None, bool]:
    """Parse %(upstream:track) output like '[ahead 1, behind 2]' or '[gone]'."""
    if not upstream:
        return None, None, False
    if track == "[gone]":
        return None, None, True
    ahead_match = re.search(r"ahead (\d+)", track)
    behind_match = re.search(r"behind (\d+)", track)
    ahead = int(ahead_match.group(1)) if ahead_match else 0
    behind = int(behind_match.group(1)) if behind_match else 0
    return ahead, behind, False


def list_refs(cwd: Path | None = None) -> list[RawRef]:
    out = run_git(
        "for-each-ref",
        "refs/heads",
        "refs/remotes/origin",
        f"--format={_REF_FORMAT}",
        cwd=cwd,
    )
    refs: list[RawRef] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        # pad: trailing empty fields (no upstream) can be stripped with the output
        parts = line.split("\t")
        parts += [""] * (7 - len(parts))
        refname, sha, email, name, date, upstream, track = parts
        if refname == "refs/remotes/origin/HEAD":
            continue
        ahead, behind, gone = _parse_track(upstream, track)
        refs.append(
            RawRef(
                refname=refname,
                sha=sha,
                author_email=email,
                author_name=name,
                committed_at=datetime.fromisoformat(date),
                ahead=ahead,
                behind=behind,
                upstream_gone=gone,
            )
        )
    return refs


def merged_ref_names(default_branch: str, cwd: Path | None = None) -> set[str]:
    """Full refnames of all refs whose tip is reachable from origin/<default>."""
    out = run_git(
        "for-each-ref",
        "--merged",
        f"origin/{default_branch}",
        "refs/heads",
        "refs/remotes/origin",
        "--format=%(refname)",
        cwd=cwd,
    )
    return {line for line in out.splitlines() if line.strip()}


def delete_local_branch(name: str, *, force: bool = False, cwd: Path | None = None) -> None:
    run_git("branch", "-D" if force else "-d", name, cwd=cwd)


def delete_remote_branch(name: str, cwd: Path | None = None) -> None:
    run_git("push", "origin", "--delete", name, cwd=cwd)


def tag_exists(tag: str, cwd: Path | None = None) -> bool:
    try:
        run_git("show-ref", "--verify", f"refs/tags/{tag}", cwd=cwd)
        return True
    except GitError:
        return False


def create_tag(tag: str, sha: str, cwd: Path | None = None) -> None:
    run_git("tag", tag, sha, cwd=cwd)


def push_tag(tag: str, cwd: Path | None = None) -> None:
    run_git("push", "origin", tag, cwd=cwd)
