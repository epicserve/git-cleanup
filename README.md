# git-cleanup

Interactively clean up git branches that are merged, done in your issue tracker, or stale.

`git-cleanup` fetches and prunes `origin`, gathers every local and remote branch (author,
age, merged status, ahead/behind counts vs upstream, and linked issue status), then opens
a full-screen TUI: one table of all branches where each row carries an action you control.

- **delete** — removes the branch locally and on origin (whatever exists). Your branches
  that are merged or whose issue is done come pre-marked.
- **archive** — creates a tag `archive/<branch>` at the tip (pushed for remote branches),
  then deletes the branch. Restore any time with `git checkout -b <branch> archive/<branch>`.
- **keep** — the default; nothing happens.

Press Enter to review everything grouped (with a prominent warning for anything deleted
on origin), confirm, and it executes. Quit with `q` and nothing changes.

### Keys

| Key | Action |
|---|---|
| ↑/↓, PgUp/PgDn | Move |
| `space` | Cycle keep → delete → archive |
| `d` / `a` / `k` | Mark delete / archive / keep |
| `o` | Open the branch's compare page on origin (vs the default branch) |
| `/` | Live filter (same syntax as `--filter`) |
| `s` | Live sort (same syntax as `--sort`) |
| `r` | Reset filter & sort to defaults |
| `Enter` | Review and confirm |
| `q` / `Esc` | Quit without changes |

Filter and sort changes are remembered per repository, so your view comes back the next
time you run `git-cleanup` there. `r` resets (and forgets) them.

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
| `--sort COLS` | Sort columns, comma-separated, `-` prefix for descending — e.g. `--sort=-age,status,author`. Columns: `branch`, `local`, `remote`, `sync`, `author`, `age`, `merged`, `issue`, `status` |
| `--filter TERMS` | Only show branches matching all terms — e.g. `--filter 'mine,age>6m,status!=done'`. A bare word matches any text column (`--filter brent`). Flags: `mine`, `merged`, `local`, `remote`, `gone` (prefix `!` to negate); `age>N`/`age<N`/`age>=N`/`age<=N` in days or with `d`/`m`/`y` suffix; substring matches `branch=X`, `author=X`, `issue=X`, `status=X` (`!=` excludes). Quote specs containing `>` or `!` |

Interactive runs default to your last-used filter and sort in that repository; an
explicit `--sort`/`--filter` wins for that session without overwriting the saved view.
Non-interactive runs (pipes, CI) use only explicit flags.
| `--config PATH` | Use an alternate config file |
| `--version` | Print the version |

Branches are matched to issues by extracting an issue key (e.g. `ABC-123`) from the
branch name, case-insensitively. Branches without a key just show no issue info.

## Safety

- The current branch, the default branch, and protected branches can never be marked
  for deletion or archiving.
- Nothing happens until you review the grouped summary and confirm it; quitting the
  TUI changes nothing.
- Remote deletions are called out in their own red-bordered warning on the review
  screen, and local deletions that would lose unpushed commits are flagged.
- Non-interactive runs (pipes, CI) never mutate anything — they print the table and exit.
- If Jira is unreachable or unconfigured, the tool degrades to git-only info
  (merged status still works).

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
