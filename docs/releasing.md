# Releasing

Pushing a tag that starts with `v` runs `.github/workflows/release.yml`. It builds the package and publishes it to TestPyPI. The real PyPI is not set up yet. The engine is the reference implementation, and a published package is not an OPEN rating.

The workflow uses Trusted Publishing. TestPyPI trusts a short-lived token that GitHub issues to this one workflow, so no upload token is stored anywhere.

## What the workflow does

1. `gate` reruns `test.yml` on the tagged commit: lint, type check, and tests on every supported Python version, plus the package build check.
2. `build` checks that the tagged commit is on `main`. It installs `build`, `hatchling`, and `twine` from the hash-pinned lock in `.github/requirements/release.txt` and builds the sdist and wheel once. Then it runs `scripts/check_sdist.py`, `scripts/check_wheel.py` (which fails unless the wheel holds only the `open_engine` modules and parameter set files and one complete dist-info directory), `scripts/check_installed_wheel.py` (run with the Python of a clean virtual environment that has only the built wheel installed, which fails unless every published parameter set loads from it), and `scripts/check_release.py`, which fails unless the tag is exactly `v` plus the version in both file names and `dist/` holds nothing else. It runs `twine check --strict` and uploads `dist/` as an artifact.
3. `publish-testpypi` runs in the `testpypi` environment. It downloads that artifact and publishes it. It is the only job with `id-token: write`, and it does not check out the repository.

Every `v*` tag publishes to TestPyPI, final versions included.

## One-time setup

A repository admin does this by hand, before the first tag is pushed.

1. **Create the GitHub environment first.** In the repository settings, under Environments, create `testpypi`. Add a required reviewer. Under deployment branches and tags, allow only tags matching `v*`. Once there is a second maintainer, turn on "Prevent self-review" so the person who tags cannot also approve. If a tag is pushed before the environment exists, GitHub creates it with no protection at all.
2. **Protect release tags.** Add a tag ruleset for `v*` that limits who can create, update, or delete those tags.
3. **Add the pending publisher on TestPyPI.** Sign in at test.pypi.org, open your account menu, then "Publishing", and add a pending GitHub publisher with these values:
   - PyPI project name: `openpickleball-engine`
   - Owner: `openpickleball`
   - Repository name: `engine`
   - Workflow name: `release.yml`
   - Environment name: `testpypi`

The first successful publish turns the pending publisher into a normal one and creates the project.

### Which control does what

GitHub runs the `release.yml` from the tagged commit. Someone who can push a tag could push one on a commit whose workflow skips `gate` and the `main` check. TestPyPI would still accept the upload, because the publisher matches the repository, the workflow file name, and the environment, not the commit or the tag. So:

- The `gate` job and the `main` check catch mistakes, such as tagging an unmerged commit or one whose tests fail.
- The tag ruleset controls who can start a release.
- The required reviewer on `testpypi` is the last check that the tagged commit is the one meant for release.
- Allowing only `v*` tags in the environment keeps branch runs out of it. It does not stop a bad `v*` tag.

## Making a release

1. In a pull request, set `__version__` in `src/open_engine/_version.py` to the new version. It is the one place the version is set: `open_engine.__version__`, the `Config.version` default, and the build all read it. A dev release such as `0.3.0.dev1` or a release candidate such as `1.0.0rc1` counts as a release. Use the canonical version form (`0.3.0.dev1`, not `0.3.0-dev1`; `1.0.0rc1`, not `1.0.0-rc1`). Update the TestPyPI install line in `README.md` to the same version; `tests/test_readme.py` fails if it pins any other. For every release, dev releases and release candidates included, move the `## [Unreleased]` entries in `CHANGELOG.md` under a new section for the version, and update the compare links at the bottom.
2. Merge it, then tag the commit on `main` and push the tag:

   ```bash
   git fetch origin && git tag v0.3.0.dev1 origin/main && git push origin v0.3.0.dev1
   ```

   If you tag a local commit instead (for example after pushing straight to `main`), push `main` first. A tag on a commit that is not yet on `main` fails the `main` check.
3. Watch the Release run. The `testpypi` approval request appears only after `build` succeeds. Before approving, check that the commit SHA of the run is the merged release commit on `main`.
4. Check the project page at https://test.pypi.org/p/openpickleball-engine.

A version can be uploaded only once. If a publish fails after the upload, or the build was wrong, bump to the next version and tag again: the next dev number for a dev release (`0.3.0.dev2`), the next candidate number for a release candidate (`1.0.0rc2`), or the next patch version for a final release (`0.3.1`). Do not move an existing tag.

## The build tool lock

`.github/requirements/release.txt` pins every build tool with hashes. Do not edit it by hand. Dependabot (the `uv` ecosystem) is set to update it weekly. To regenerate it yourself, run the command in its header on Linux:

```bash
uv pip compile .github/requirements/release.in --generate-hashes --python-version 3.12 -o .github/requirements/release.txt
```

The command resolves for the platform it runs on, and the lock has no platform markers. Run on Linux, it matches the runner the workflows use; run on macOS or Windows, it drops Linux-only packages and the install on the runner fails. Recompile it if a workflow moves to another Python version or operating system. Dependabot may rewrite the command in the header in a slightly different form. That is fine as long as it keeps `--generate-hashes` and `--python-version 3.12`.
