# CLAUDE.md - Release Workflow

When the user asks to "make a release", follow these steps:

1. **Update CHANGELOG.md**: add a new `## [X.Y.Z] - YYYY-MM-DD` section at the top
   (below the header) using today's date, with `### Added` / `### Changed` / `### Fixed`
   subsections as appropriate. Use `git log v<last-version>..HEAD --oneline` to see what
   shipped since the last release. Add the release link at the bottom of the file.

2. **Bump the version**: run `just version_bump {major|minor|patch}` — ask the user
   which bump they want if they didn't specify. This runs `uv version --bump` (updating
   `pyproject.toml` and `uv.lock`), stages those files plus `CHANGELOG.md`, commits
   `Bump version to vX.Y.Z`, and creates the annotated tag `vX.Y.Z`.

3. **Push**: `git push origin main vX.Y.Z`

4. **Automation takes over**: the tag push triggers `.github/workflows/release.yml`,
   which runs lint + tests, builds and smoke-tests the wheel, publishes to PyPI via
   trusted publishing (OIDC — there is no PyPI token secret), and then creates the
   GitHub Release with auto-generated notes.

5. **Verify**: the Actions run is green, https://pypi.org/project/git-cleanup/ shows
   the new version, and `uvx git-cleanup@latest --version` prints it.

## Notes

- The version number lives only in `pyproject.toml`. `git_cleanup.__version__` reads it
  from installed package metadata at runtime — never hand-edit a version string anywhere
  else.
- PyPI publishing uses a trusted publisher bound to repo `epicserve/git-cleanup`,
  workflow `release.yml`, environment `pypi`. If publishing fails with an OIDC error,
  check that those three values still match exactly on pypi.org.
- If the release workflow fails **before** the publish step: fix the problem, delete the
  tag (`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`), and start over.
  If it fails **after** publishing: do not reuse the version number — PyPI uploads are
  write-once. Bump patch and release again.
