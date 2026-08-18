"""All git subprocess interactions.

Every read is a constant number of git calls regardless of branch count, with
two documented exceptions, both one call per item rather than per branch:

- `worktree list` does not report cleanliness, so `worktree_dirty_count` costs
  one `git status` per worktree.
- `stash list` does not report size, so `stash_file_count` costs one
  `git stash show` per stash.

Both are acceptable where a per-branch call would not be — branch counts are
unbounded, while worktrees are hand-made checkouts and stashes are hand-made
snapshots, and both number in the single digits for most repos.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
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


@dataclass(frozen=True)
class RawWorktree:
    path: Path
    head: str | None = None
    branch: str | None = None  # full refname, e.g. refs/heads/foo
    bare: bool = False
    detached: bool = False
    locked: bool = False
    lock_reason: str = ""
    prunable: bool = False
    prune_reason: str = ""

    @property
    def short_branch(self) -> str | None:
        if self.branch is None:
            return None
        return self.branch.removeprefix("refs/heads/")


@dataclass(frozen=True)
class RawStash:
    index: int  # reflog position — NOT a stable id; see list_stashes
    selector: str  # raw %gd, e.g. "stash@{0}"
    sha: str
    created_at: datetime
    parents: tuple[str, ...]
    subject: str  # raw %gs


def _run_git_unchecked(
    args: Sequence[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run git and hand back the result whatever the exit code.

    Only for the handful of commands whose *failure* carries information we
    need — see restore_stash, where the two failure modes share an exit code
    but write to different streams.
    """
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _run_git(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run git, raising GitError on a nonzero exit. Use run_git unless you need
    stderr (a few git commands report on it even on success)."""
    result = _run_git_unchecked(args, cwd)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def run_git(*args: str, cwd: Path | None = None) -> str:
    return _run_git(args, cwd).stdout.strip()


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


def _parse_worktree_records(out: str, separator: str) -> list[RawWorktree]:
    """Parse `worktree list --porcelain` output into records.

    Attributes are `separator`-terminated, each either "label value" or a bare
    label for booleans; an empty attribute ends a record. Unknown labels from a
    future git are ignored.
    """
    worktrees: list[RawWorktree] = []
    fields: dict = {}

    def flush() -> None:
        nonlocal fields
        # idempotent: -z output ends with a record terminator, but run_git's
        # .strip() eats the newline form's trailing blank line, so the final
        # record is only flushed by the unconditional call at the end
        if not fields:
            return
        worktrees.append(RawWorktree(**fields))
        fields = {}

    for attribute in out.split(separator):
        label, _, value = attribute.partition(" ")
        match label:
            case "":
                flush()
            case "worktree":
                flush()  # defensive: a record not preceded by an empty attribute
                fields["path"] = Path(value)
            case "HEAD":
                fields["head"] = value
            case "branch":
                fields["branch"] = value
            case "bare":
                fields["bare"] = True
            case "detached":
                fields["detached"] = True
            case "locked":
                fields["locked"] = True
                fields["lock_reason"] = value
            case "prunable":
                fields["prunable"] = True
                fields["prune_reason"] = value
    flush()
    return worktrees


def list_worktrees(cwd: Path | None = None) -> list[RawWorktree]:
    """Every worktree of this repo; git documents the main one as first.

    Never pass --expire: it reports merely old-but-live worktrees as prunable,
    and callers route prunable entries into `git worktree prune`.
    """
    try:
        out = run_git("worktree", "list", "--porcelain", "-z", cwd=cwd)
    except GitError:  # -z landed in git 2.36; fall back (reasons may arrive quoted)
        return _parse_worktree_records(run_git("worktree", "list", "--porcelain", cwd=cwd), "\n")
    return _parse_worktree_records(out, "\0")


def worktree_dirty_count(path: Path) -> int | None:
    """Number of `git status --porcelain` entries, or None if git can't look.

    Uses `git -C` rather than run_git(cwd=path): a prunable worktree's directory
    is gone, and subprocess's cwd= would raise FileNotFoundError, which is not a
    GitError and would escape every caller's handler. `git -C` exits 128 instead.
    --ignore-submodules=none matches `worktree remove`'s own cleanliness check
    (builtin/worktree.c), so 0 reliably predicts "no --force needed".
    """
    try:
        out = run_git(
            "--no-optional-locks",  # don't refresh that worktree's index under an editor
            "-C",
            str(path),
            "status",
            "--porcelain",
            "--ignore-submodules=none",
        )
    except GitError:
        return None
    return len(out.splitlines())


def remove_worktree(path: Path, *, force: bool = False, cwd: Path | None = None) -> None:
    """Remove a worktree's directory and bookkeeping. Never touches its branch.

    Only ever a single --force (for uncommitted changes); removing a locked
    worktree would need -f -f, and callers filter locked worktrees out instead.
    """
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    run_git(*args, str(path), cwd=cwd)


def prune_worktrees(*, dry_run: bool = False, cwd: Path | None = None) -> list[str]:
    """Clear every prunable administrative entry, returning git's report lines.

    Repo-wide, not per-path: there is no way to prune one broken entry alone.
    Locked worktrees are skipped by git.
    """
    args = ["worktree", "prune", "--verbose"]
    if dry_run:
        args.append("--dry-run")
    result = _run_git(args, cwd)
    # --verbose reports on stderr, not stdout, for both real and dry runs
    return [line for line in result.stderr.splitlines() if line.strip()]


# %gs, not %s: %gs is the reflog subject that `git stash list` itself prints, and
# the two diverge for stashes made with `git stash store`.
# NEVER add --date=: it rewrites %gd from "stash@{0}" into
# "stash@{2026-07-31 10:30:14 -0500}", destroying the only usable selector.
# %cI is immune to it, and parses with the same fromisoformat idiom as list_refs.
_STASH_FORMAT = "%gd%x1f%H%x1f%cI%x1f%P%x1f%gs"
_STASH_SELECTOR_RE = re.compile(r"^stash@\{(\d+)\}$")


def _parse_stash_records(out: str) -> list[RawStash]:
    """Parse \\x1f-separated `stash list` output into records.

    Same trap as _parse_worktree_records: -z *terminates* each record rather
    than separating them, and run_git's .strip() does not eat NUL (it is not
    Python whitespace), so the split yields a trailing empty element. Reflog
    subjects are single-line by construction, so accepting the newline form as
    a fallback costs nothing and needs no second git call.
    """
    separator = "\0" if "\0" in out else "\n"
    stashes: list[RawStash] = []
    for record in out.split(separator):
        if not record:
            continue
        # maxsplit: a subject cannot contain \x1f in practice, and capping the
        # split makes mis-splitting on one structurally impossible
        selector, sha, date, parents, subject = record.split("\x1f", 4)
        match = _STASH_SELECTOR_RE.match(selector)
        if match is None:
            # loud, not lenient: a selector we cannot number is a selector the
            # executor could point at the wrong stash. Callers degrade a
            # GitError to "no stashes", which is the safe failure.
            raise GitError(f"unexpected stash selector {selector!r}")
        stashes.append(
            RawStash(
                index=int(match[1]),
                selector=selector,
                sha=sha,
                created_at=datetime.fromisoformat(date),
                parents=tuple(parents.split()),
                subject=subject,
            )
        )
    return stashes


def list_stashes(cwd: Path | None = None) -> list[RawStash]:
    """Every stash, in reflog order (stash@{0} first) — never date order.

    A selector is a reflog *position*, not an id: dropping stash@{1} renumbers
    everything above it. Callers that mutate must work in descending index
    order. An empty stash list is exit 0 with zero bytes, not an error.
    """
    return _parse_stash_records(
        run_git("stash", "list", "-z", f"--format={_STASH_FORMAT}", cwd=cwd)
    )


def stash_file_count(ref: str, cwd: Path | None = None) -> int | None:
    """Number of files a stash touches, or None if git could not look.

    --include-untracked unconditionally: the default `stash show` omits
    untracked files entirely, and the flag is a no-op on stashes without any.
    """
    try:
        out = run_git("stash", "show", "--include-untracked", "--name-status", ref, cwd=cwd)
    except GitError:
        return None
    return len([line for line in out.splitlines() if line.strip()])


def stash_patch(ref: str, cwd: Path | None = None) -> str:
    """Diffstat plus patch for a stash, for the detail pane.

    `git stash show -p` and not `git show`: the latter renders a stash's merge
    commit as combined `diff --cc` format, which is unreadable and omits hunks.
    """
    return run_git("stash", "show", "--include-untracked", "--stat", "-p", ref, cwd=cwd)


def stash_sha(selector: str, cwd: Path | None = None) -> str:
    """The commit a selector points at *now*.

    Raises GitError (exit 128) once the reflog is shorter than the selector's
    index — which is how a stale decision is detected before acting on it.
    """
    return run_git("rev-parse", "--verify", selector, cwd=cwd)


def conflicted_paths(cwd: Path | None = None) -> list[str]:
    """Paths with unresolved merge conflicts.

    Structural rather than message-sniffing, so it is immune to git's
    localized output.
    """
    out = run_git("diff", "--name-only", "--diff-filter=U", cwd=cwd)
    return [line for line in out.splitlines() if line.strip()]


def drop_stash(selector: str, cwd: Path | None = None) -> None:
    """Discard a stash without applying it.

    The commit stays reachable until the next gc, so `git stash store <sha>`
    can undo this.
    """
    run_git("stash", "drop", selector, cwd=cwd)


@dataclass(frozen=True)
class RestoreResult:
    ok: bool
    conflicted: bool
    detail: str


def restore_stash(selector: str, *, keep: bool, cwd: Path | None = None) -> RestoreResult:
    """`git stash apply` (keep=True) or `git stash pop` (keep=False).

    Returns instead of raising, because the failure modes share exit code 1 but
    not their output stream:

    - a dirty tree that would be clobbered: git refuses, and the message is on
      stderr;
    - a real merge conflict: git *applies* with conflict markers, prints
      "CONFLICT" to stdout, and leaves stderr empty.

    A GitError's stderr-only message would therefore be blank for the case that
    actually changed the working tree. Conflicts are detected structurally, via
    unmerged index entries, rather than by grepping git's localizable text.

    In every failure mode the stash stays in the list. There is also a third
    mode (untracked files colliding with existing ones) that exit code alone
    cannot distinguish, so callers must not claim the tree was left untouched.

    Note that refs/stash is repo-global but a restore writes into the *current*
    worktree, so running from a linked worktree restores there.
    """
    result = _run_git_unchecked(["stash", "apply" if keep else "pop", selector], cwd)
    if result.returncode == 0:
        return RestoreResult(ok=True, conflicted=False, detail=result.stdout.strip())
    detail = result.stderr.strip() or result.stdout.strip()
    return RestoreResult(ok=False, conflicted=bool(conflicted_paths(cwd)), detail=detail)


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
