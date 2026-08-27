# git-cleanup

[![PyPI version](https://img.shields.io/pypi/v/git-cleanup)](https://pypi.org/project/git-cleanup/)
[![Python versions](https://img.shields.io/pypi/pyversions/git-cleanup)](https://pypi.org/project/git-cleanup/)
[![CI](https://github.com/epicserve/git-cleanup/actions/workflows/ci.yml/badge.svg)](https://github.com/epicserve/git-cleanup/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Interactively clean up git branches that are merged, done in your issue tracker, or stale.

`git-cleanup` fetches and prunes `origin`, gathers every local and remote branch (author,
age, merged status, ahead/behind counts vs upstream, and linked issue status), then opens
a full-screen TUI with three tabs. **Branches** is one table of all branches where each row
carries an action you control. **Worktrees** lists every `git worktree` with the same
branch decision columns as Branches (plus worktree flags), and shows the full path of
the highlighted row under the table. **Stashes** lists every stash with its message,
origin branch, age, and file count, alongside a live diff pane so you can read a stash
before deciding its fate.

### Branches

- **delete** — removes the branch locally and on origin (whatever exists). Your branches
  that are merged or whose issue is done come pre-marked.
- **delete-local** — removes only your local branch and leaves `origin/<branch>` in place.
  For treating local branches as your active workspace: clear one off your machine because
  the remote still has it. Nobody else is affected. Press `d` a second time to get here.
  Branches that exist on only one side skip this action — `delete` is already single-sided.
- **archive** — creates a tag `archive/<branch>` at the tip (pushed for remote branches),
  then deletes the branch. Restore any time with `git checkout -b <branch> archive/<branch>`.
- **keep** — the default; nothing happens.

### Worktrees

- **remove** — runs `git worktree remove`, with `--force` when the worktree has
  uncommitted changes. A worktree whose directory is already gone is cleared with
  `git worktree prune` instead. Worktrees whose branch is merged or issue-done come
  pre-marked, as do broken entries — but a worktree with uncommitted changes never does.
- **keep** — the default; nothing happens.

Removing a worktree **never deletes its branch** — do that on the Branches tab. Worktree
removals run **before** branch deletions, so marking both in one session works: `git
branch -d` refuses a branch that is checked out anywhere, and the review screen tells you
which deletions are waiting on which removal.

### Stashes

- **drop** — discards the stash without restoring it. Recoverable: git keeps the commit
  reachable until the next `gc`, and the review screen prints the
  `git stash store <sha>` you would need.
- **pop** — restores the stash into your working tree, then removes it from the list.
- **apply** — restores it but keeps it in the list.
- **keep** — the default; nothing happens.

Stashes are **never pre-marked** — a stash is uncommitted work by definition, there is no
"merged" signal to key off, and an old stash is exactly the one you forgot about but may
still want. Only **one pop or apply per run** is allowed: a restore is the only action here
that writes to your working tree, and a failed one leaves it dirty, which would make every
restore after it fail too. Drops are unlimited.

Press Enter to review everything grouped (with a prominent warning for anything deleted
on origin or removed with `--force`), confirm, and it executes. Quit with `q` and nothing
changes.

### Keys

| Key | Action |
|---|---|
| ↑/↓, PgUp/PgDn | Move |
| `[` / `]` | Previous / next tab |
| `b` / `w` / `t` | Jump straight to Branches / Worktrees / Stashes |
| `space` | Cycle keep → delete → delete-local → archive (Worktrees: toggle keep ⇄ remove; Stashes: cycle keep → drop → pop → apply) |
| `d` | Mark delete; press again to toggle between delete and delete-local (Worktrees: mark remove; Stashes: mark drop) |
| `a` / `k` | Mark archive / keep (Stashes: `a` marks apply) |
| `p` | Mark pop (Stashes only) |
| `o` | Open the branch's compare page on origin (vs the default branch) |
| `/` | Live filter (same syntax as `--filter`) |
| `s` | Live sort (same syntax as `--sort`) |
| `r` | Reset filter & sort to defaults |
| `Ctrl+D` / `Ctrl+U` | Scroll the stash diff pane |
| `Enter` | Review and confirm |
| `q` / `Esc` | Quit without changes |

The footer always shows the keys for the tab you are on, and advertises `[`/`]` for tab
switching; `b`/`w`/`t` work too but stay out of the footer to keep it to one line. `/`, `s`,
`r`, and `o` are branch-only — worktree and stash lists are short enough not to need
filtering or sorting, and stashes must never be reordered, because their `stash@{N}`
numbering is positional.

On the Stashes tab the diff pane sits to the right of the table at 100 columns or wider,
and moves below it on narrower terminals so the table always has room for every column.
On the Worktrees tab the full path of the highlighted row sits under the table, so the
decision columns can match Branches without crowding the path into the grid.

Filter and sort changes are remembered per repository, so your view comes back the next
time you run `git-cleanup` there. `r` resets (and forgets) them.

A `delete-local` branch still exists on origin, so the next scan lists it again as a
remote-only row. If you treat local branches as your active workspace, filter to `local`
(press `/`, type `local`) to see only branches you actually have checked out — that filter
is remembered per repo, so the branches you have cleared off your machine stay out of view.

Jira is the built-in issue tracker for now; the provider layer is designed so GitHub
Issues, Linear, etc. can be added later. The scan pipeline (`git_cleanup.core.scan_repo`
+ `planner.recommend_actions`) is UI-free and importable, so CI jobs can generate branch
reports from the same data.

## Install

Requires Python 3.14+ and git.

```console
$ uv tool install git-cleanup   # install the CLI on your PATH
$ uvx git-cleanup               # or run it without installing
```

You can also install it with `pip install git-cleanup` if you prefer.

## Usage

```console
$ uvx git-cleanup            # in any git repo with an origin remote
$ uvx git-cleanup --dry-run  # preview everything, change nothing
```

### Flags

| Flag | Effect |
|---|---|
| `--dry-run` | Full run with zero mutations — prints `[dry-run] would delete ...` instead |
| `--no-fetch` | Skip the initial `git fetch --prune origin` |
| `--all` | Pre-mark other authors' cleanup-eligible branches for deletion too |
| `--sort COLS` | Sort columns, comma-separated, `-` prefix for descending — e.g. `--sort=-age,status,author`. Columns: `branch`, `local`, `remote`, `worktree`, `sync`, `author`, `age`, `merged`, `issue`, `status` |
| `--filter TERMS` | Only show branches matching all terms — e.g. `--filter 'mine,age>6m,status!=done'`. A bare word matches any text column (`--filter brent`). Flags: `mine`, `merged`, `local`, `remote`, `gone`, `worktree` (prefix `!` to negate); `age>N`/`age<N`/`age>=N`/`age<=N` in days or with `d`/`m`/`y` suffix; substring matches `branch=X`, `author=X`, `issue=X`, `status=X` (`!=` excludes). `|` ORs alternatives inside one text term (`author=sam|chris`, or a bare `sam|chris`). An empty value tests whether the column is set at all — `status=` keeps only branches with no status, `status!=` only those that have one. Quote specs containing `>`, `!`, or `|` |
| `--config PATH` | Use an alternate config file |
| `--version` | Print the version |

Interactive runs default to your last-used filter and sort in that repository; an
explicit `--sort`/`--filter` wins for that session without overwriting the saved view.
Non-interactive runs (pipes, CI) use only explicit flags.

`worktree` is a boolean filter flag, so the bare word `worktree` now means "has a
worktree" rather than a substring search across the text columns, and `!worktree` means
"has no worktree". To search for the literal string, use `branch=worktree`.

Branches are matched to issues by extracting an issue key (e.g. `ABC-123`) from the
branch name, case-insensitively. Branches without a key just show no issue info.

## Safety

- The current branch, the default branch, and protected branches can never be marked
  for deletion or archiving.
- Nothing happens until you review the grouped summary and confirm it; quitting the
  TUI changes nothing.
- Remote deletions are called out in their own red-bordered warning on the review
  screen, and local deletions that would lose unpushed commits are flagged.
- `delete-local` rows never appear in that warning (nothing leaves origin); they are
  listed with the `origin/<branch>` they are keeping.
- Non-interactive runs (pipes, CI) never mutate anything — they print the tables and exit.
- If Jira is unreachable or unconfigured, the tool degrades to git-only info
  (merged status still works).

For worktrees specifically:

- The main worktree, the worktree you are currently in, and locked worktrees can never
  be marked — git cannot remove them, and the tab says so when you try.
- A worktree with uncommitted changes *can* be marked by hand, but it is never pre-marked,
  it is flagged in its own red-bordered panel on the review screen, and removing it passes
  `--force`, which discards that work irrecoverably.
- `git worktree prune` is repo-wide, so clearing one broken entry clears them all. The
  dry-run note and the summary report git's own list rather than the count you marked.
- Worktree removals execute before any branch deletion, and removing a worktree never
  deletes its branch.
- If listing worktrees fails for an unrelated reason, the run degrades to branches-only
  with a warning on stderr.

For stashes specifically:

- Nothing is ever pre-marked, and only one pop/apply is allowed per run.
- A `stash@{N}` selector is a reflog *position*, not an id: dropping `stash@{1}` renumbers
  `{2}` to `{1}`. Marked stashes are therefore executed in **descending index order**, so
  the ones not yet touched never move, and each is re-checked against the commit it pointed
  at during the scan — a stash that changed underneath you (say, popped in another
  terminal) is skipped with a warning rather than acted on.
- Dropping is **recoverable** until git's next `gc`: the review screen prints the
  `git stash store <sha>` that would bring one back.
- git allows restoring a stash onto a **different branch** than it was made on with no
  warning of its own, so the tab colors the mismatch and the review screen spells it out.
- A failed pop always leaves the stash in the list. If it conflicted, the conflict markers
  are in your files and the stash is still there to retry or drop by hand.
- A restore lands in whichever worktree you ran `git-cleanup` from, since `refs/stash` is
  repo-global but the restore writes to the current working tree.
- If listing stashes fails, the run degrades to no Stashes tab with a warning on stderr.

## Configuration

`~/.config/git-cleanup/config.toml` (respects `$XDG_CONFIG_HOME`):

```toml
[tracker]
provider = "jira"          # or "none" to disable issue lookups

[jira]
url = "https://yourcompany.atlassian.net"
email = "you@yourcompany.com"
api_token = "..."          # create one at https://id.atlassian.com/manage-profile/security/api-tokens

[cleanup]
protected_branches = ["main", "master", "develop"]
done_statuses = []         # extra status names to treat as done, e.g. ["Won't Do"]
archive_age_days = 90      # minimum age for the archive prompt
```

Any setting can be overridden per repository with a `[repos."<path>"]` table in the
same file — the key is the repository's root path (`~` expands), and its values merge
key-by-key over the global sections:

```toml
[repos."~/Code/some-repo".cleanup]
protected_branches = ["main", "staging"]
archive_age_days = 30

[repos."~/Code/other-repo".tracker]
provider = "none"
```

Environment variables `JIRA_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN` override the
config file, including repo overrides.

The Worktrees tab adds no configuration of its own: `archive_age_days` also drives the
stale highlight on its Age column, and since that is already per-repo overridable, a
monorepo with long-lived worktrees just raises it. Note that `protected_branches` does
*not* prevent removing a worktree — removing a checkout never touches a ref — but a
protected branch's worktree is never pre-marked, only manually markable.

Worktree listing uses `git worktree list --porcelain -z` (git 2.36+), falling back to the
newline form on older git.

Filter/sort views chosen in the TUI are saved per repository in
`$XDG_STATE_HOME/git-cleanup/state.json` (default `~/.local/state/git-cleanup/state.json`).

## Development

```console
$ uv sync
$ uv run pytest
$ uv run git-cleanup --dry-run
```

Tests run against real temporary git repositories and a mocked Jira API — no network
or credentials needed.

With [just](https://github.com/casey/just) installed, `just format`, `just lint`, and
`just test` wrap the common tasks, and `just pre_commit` runs all three. Releases are
cut with `just version_bump <major|minor|patch>` followed by pushing the tag — see
[CLAUDE.md](CLAUDE.md) for the full release process.

## License

[MIT](LICENSE). Provided as is, without warranty of any kind — see the LICENSE file
for the full text.
