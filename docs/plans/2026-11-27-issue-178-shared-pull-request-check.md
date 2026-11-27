# Plan: Run the organization's shared pull request check (issue #178)

Risk tier: Low. CI configuration and contributor docs only: no engine code, `Config` default, public API, or published rating changes.

## Acceptance criteria

1. `.github/workflows/pull-request.yml` calls `openpickleball/.github/.github/workflows/pull-request.yml`, pinned to the `v1.0.0` commit with the version in a comment, on opened, edited, synchronize, and reopened, with read-only top-level permissions.
2. `pull-request / check` runs and passes on the pull request that adds it.
3. CLAUDE.md lists the check next to the `sign-off` check, and its commit message rule says no type prefix such as `docs:`, as the organization's contributing guide does.

## Files that change

- `.github/workflows/pull-request.yml`: new.
- `CLAUDE.md`: the CI paragraph and the commit message rule.

## Risks

- Squash merges append ` (#N)` to the subject on `main`, which can pass 72 characters. The check reads only a pull request's own commits, so those merge commits are not checked.
- Not done: no change to `CHANGELOG.md`, since nothing the package ships changes, and no ruleset change, which a maintainer makes after merge.
