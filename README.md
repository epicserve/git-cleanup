# git-cleanup

Interactively clean up git branches that are merged, done in your issue tracker, or stale.

`git-cleanup` fetches and prunes `origin`, gathers every local and remote branch (author,
age, merged status, ahead/behind counts vs upstream, and linked issue status), then walks
you through three prompts:

1. **Delete your local branches** that are merged or whose issue is done — pre-checked,
   unselect anything you want to keep.
2. **Delete branches on origin** that are no longer needed — with an extra confirmation
   before anything remote is touched.
3. **Archive old branches** you want to keep but won't work on — creates a tag
   `archive/<branch>` at the tip, then deletes the branch. Restore any time with
   `git checkout -b <branch> archive/<branch>`.

Jira is the built-in issue tracker for now; the provider layer is designed so GitHub
Issues, Linear, etc. can be added later.

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
| `--all` | Include branches authored by others in the cleanup prompts |
| `--sort COLS` | Sort columns, comma-separated, `-` prefix for descending — e.g. `--sort=-age,status,author`. Columns: `branch`, `local`, `remote`, `sync`, `author`, `age`, `merged`, `issue`, `status` |
| `--filter TERMS` | Only show branches matching all terms — e.g. `--filter 'mine,age>6m,status!=done'`. A bare word matches any text column (`--filter brent`). Flags: `mine`, `merged`, `local`, `remote`, `gone` (prefix `!` to negate); `age>N`/`age<N`/`age>=N`/`age<=N` in days or with `d`/`m`/`y` suffix; substring matches `branch=X`, `author=X`, `issue=X`, `status=X` (`!=` excludes). Quote specs containing `>` or `!` |
| `--config PATH` | Use an alternate config file |
| `--version` | Print the version |

Branches are matched to issues by extracting an issue key (e.g. `ABC-123`) from the
branch name, case-insensitively. Branches without a key just show no issue info.

## Safety

- The current branch, the default branch, and protected branches are never offered
  for deletion or archiving.
- Every prompt is a checkbox list — nothing is deleted without your selection.
- Remote deletion always requires a second explicit confirmation.
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

Environment variables `JIRA_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN` override the
config file.

## Development

```console
$ uv sync
$ uv run pytest
$ uv run git-cleanup --dry-run
```

Tests run against real temporary git repositories and a mocked Jira API — no network
or credentials needed.
