# git-cleanup

Interactively clean up git branches that are merged, done in your issue tracker, or stale.

`git-cleanup` fetches and prunes `origin`, gathers every local and remote branch (author,
age, merged status, ahead/behind counts vs upstream, and linked issue status), then opens
a full-screen TUI with two tabs. **Branches** is one table of all branches where each row
carries an action you control. **Worktrees** lists every `git worktree` with its branch,
its count of uncommitted changes, and whether it is broken or locked.

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

Press Enter to review everything grouped (with a prominent warning for anything deleted
on origin or removed with `--force`), confirm, and it executes. Quit with `q` and nothing
changes.

### Keys

| Key | Action |
|---|---|
| ↑/↓, PgUp/PgDn | Move |
| `b` / `w` | Switch to the Branches / Worktrees tab |
| `space` | Cycle keep → delete → delete-local → archive (Worktrees: toggle keep ⇄ remove) |
| `d` | Mark delete; press again to toggle between delete and delete-local (Worktrees: mark remove) |
| `a` / `k` | Mark archive / keep |
| `o` | Open the branch's compare page on origin (vs the default branch) |
| `/` | Live filter (same syntax as `--filter`) |
| `s` | Live sort (same syntax as `--sort`) |
| `r` | Reset filter & sort to defaults |
| `Enter` | Review and confirm |
| `q` / `Esc` | Quit without changes |

The footer always shows the keys for the tab you are on. `/`, `s`, `r`, and `o` are
branch-only — worktree lists are short enough not to need filtering or sorting.

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
| `--filter TERMS` | Only show branches matching all terms — e.g. `--filter 'mine,age>6m,status!=done'`. A bare word matches any text column (`--filter brent`). Flags: `mine`, `merged`, `local`, `remote`, `gone`, `worktree` (prefix `!` to negate); `age>N`/`age<N`/`age>=N`/`age<=N` in days or with `d`/`m`/`y` suffix; substring matches `branch=X`, `author=X`, `issue=X`, `status=X` (`!=` excludes). Quote specs containing `>` or `!` |
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
