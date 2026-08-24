# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-24

### Added

- Text filter terms accept `|` as OR, so `--filter 'author=sam|chris'` (or a bare
  `sam|chris`) matches any alternative. Comma-separated terms are still AND.
  Quote specs containing `|`.

## [0.1.1] - 2026-08-18

### Changed

- Upgraded the ruff dev dependency to 0.16 and fixed all findings from its expanded
  default rule set (internal code cleanups; no user-facing behavior changes).

## [0.1.0] - 2026-08-18

### Added

- Interactive full-screen TUI for cleaning up a git repository, with three tabs:
  **Branches**, **Worktrees**, and **Stashes**.
- Branch actions: **delete** (local + origin), **delete-local** (leave origin alone),
  **archive** (tag as `archive/<branch>`, then delete), and **keep**. Merged branches
  and branches whose linked issue is done come pre-marked for deletion.
- Jira integration: branch names containing an issue key (e.g. `ABC-123`) show the
  issue's status, and done issues drive cleanup recommendations. The tracker layer is
  pluggable so other providers can be added later.
- Worktrees tab: remove stale or broken worktrees (`git worktree remove` / `prune`),
  with uncommitted changes clearly flagged and never pre-marked.
- Stashes tab: drop, pop, or apply stashes with a live diff pane; never pre-marked,
  and at most one pop/apply per run for safety.
- Safety model: nothing mutates until a grouped review screen is confirmed;
  remote deletions and forced worktree removals get red-bordered warnings;
  protected/current/default branches can never be marked; non-interactive runs
  never mutate anything.
- `--dry-run`, `--no-fetch`, `--all`, `--sort`, `--filter`, `--config`, and
  `--version` flags; filter and sort views are remembered per repository.
- Configuration via `~/.config/git-cleanup/config.toml` with per-repository
  overrides and `JIRA_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` environment variables.

[0.2.0]: https://github.com/epicserve/git-cleanup/releases/tag/v0.2.0
[0.1.1]: https://github.com/epicserve/git-cleanup/releases/tag/v0.1.1
[0.1.0]: https://github.com/epicserve/git-cleanup/releases/tag/v0.1.0
