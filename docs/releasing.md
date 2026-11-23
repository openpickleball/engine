# Releasing

Pushing a tag that starts with `v` runs `.github/workflows/release.yml`. It builds the package, publishes it to TestPyPI, and then publishes the same files to PyPI. Each publish waits for its own approval. The engine is the reference implementation, and a published package is not an OPEN rating.

The workflow uses Trusted Publishing. TestPyPI and PyPI each trust a short-lived token that GitHub issues to this one workflow and environment, so no upload token is stored anywhere.

## What the workflow does

1. `gate` reruns `test.yml` on the tagged commit: lint, type check, and tests on every supported Python version, plus the package build check.
2. `build` checks that the tagged commit is on `main`. It installs `build`, `hatchling`, and `twine` from the hash-pinned lock in `.github/requirements/release.txt` and builds the sdist and wheel once. Then it runs `scripts/check_sdist.py`, `scripts/check_wheel.py` (which fails unless the wheel holds only the `open_engine` modules and parameter set files and one complete dist-info directory), `scripts/check_installed_wheel.py` (run with the Python of a clean virtual environment that has only the built wheel installed, which fails unless every published parameter set loads from it), and `scripts/check_release.py`, which fails unless the tag is exactly `v` plus the version in both file names and `dist/` holds nothing else. It runs `twine check --strict` and uploads `dist/` as an artifact.
3. `publish-testpypi` runs in the `testpypi` environment. It downloads that artifact and publishes it to TestPyPI.
4. `publish-pypi` runs in the `pypi` environment, only after `publish-testpypi` succeeds. It downloads the same artifact and publishes it to PyPI.

The two publish jobs are the only jobs with `id-token: write`, and neither checks out the repository. Every `v*` tag publishes to TestPyPI and then, once approved, to PyPI, release candidates and final versions included. If TestPyPI fails, nothing is published to PyPI.

## One-time setup

A repository admin does this by hand, before the first tag is pushed.

1. **Create the GitHub environments first.** In the repository settings, under Environments, create `testpypi` and `pypi`, and set up each the same way. Add a required reviewer. Under deployment branches and tags, allow only tags matching `v*`. Once there is a second maintainer, turn on "Prevent self-review" so the person who tags cannot also approve. If a tag is pushed before an environment exists, GitHub creates it with no protection at all.
2. **Protect release tags.** Add a tag ruleset for `v*` that limits who can create, update, or delete those tags.
3. **Add the pending publisher on TestPyPI.** Sign in at test.pypi.org, open your account menu, then "Publishing", and add a pending GitHub publisher with these values:
   - PyPI project name: `openpickleball-engine`
   - Owner: `openpickleball`
   - Repository name: `engine`
   - Workflow name: `release.yml`
   - Environment name: `testpypi`
4. **Add the pending publisher on PyPI.** Do the same at pypi.org, with the same values except the environment:
   - PyPI project name: `openpickleball-engine`
   - Owner: `openpickleball`
   - Repository name: `engine`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

   Do this before pushing the first tag that should reach PyPI. Without it, `publish-pypi` fails after TestPyPI has already taken the version. If that happens, register the publisher on pypi.org and, within 30 days of the run, use "Re-run failed jobs" on it, not "Re-run all jobs": TestPyPI rejects a second upload of the same version.

The first successful publish to each index turns its pending publisher into a normal one and creates the project there.

### Which control does what

GitHub runs the `release.yml` from the tagged commit. Someone who can push a tag could push one on a commit whose workflow skips `gate` and the `main` check. TestPyPI and PyPI would still accept the upload, because each publisher matches the repository, the workflow file name, and the environment, not the commit or the tag. So:

- The `gate` job and the `main` check catch mistakes, such as tagging an unmerged commit or one whose tests fail.
- The tag ruleset controls who can start a release.
- The required reviewers on `testpypi` and `pypi` are the last checks that the tagged commit is the one meant for release. Approving `testpypi` does not approve `pypi`: PyPI gets its own approval, after the TestPyPI upload can be checked.
- Allowing only `v*` tags in each environment keeps branch runs out of it. It does not stop a bad `v*` tag.

## Making a release

1. In a pull request, set `__version__` in `src/open_engine/_version.py` to the new version. It is the one place the version is set: `open_engine.__version__`, the `Config.version` default, and the build all read it. A dev release such as `0.3.0.dev1` or a release candidate such as `1.0.0rc1` counts as a release. Use the canonical version form (`0.3.0.dev1`, not `0.3.0-dev1`; `1.0.0rc1`, not `1.0.0-rc1`). Update the TestPyPI install line in `README.md` to the same version; `tests/test_readme.py` fails if it pins any other. For every release, dev releases and release candidates included, move the `## [Unreleased]` entries in `CHANGELOG.md` under a new section for the version, and update the compare links at the bottom.
2. Merge it, then tag the commit on `main` and push the tag:

   ```bash
   git fetch origin && git tag v0.3.0.dev1 origin/main && git push origin v0.3.0.dev1
   ```

   If you tag a local commit instead (for example after pushing straight to `main`), push `main` first. A tag on a commit that is not yet on `main` fails the `main` check.
3. Watch the Release run. The `testpypi` approval request appears only after `build` succeeds. Before approving, check that the commit SHA of the run is the merged release commit on `main`.
4. Check the project page at https://test.pypi.org/p/openpickleball-engine, then install the release from TestPyPI in a clean virtual environment outside the checkout and load a published parameter set. The package has no dependencies, so TestPyPI alone is enough:

   ```bash
   python3 -m venv --clear /tmp/open-engine-check && /tmp/open-engine-check/bin/pip install --no-cache-dir -i https://test.pypi.org/simple/ openpickleball-engine==1.0.0rc2 && cd /tmp && /tmp/open-engine-check/bin/python -I -c "import open_engine; print(open_engine.__version__, open_engine.Config.published('v1').parameter_fingerprint())"
   ```

   Use the version you tagged. It prints that version and `v1`'s parameter fingerprint, `f9149f3c38a01efc`. A release candidate installs only when the version is pinned, as here, or with `pip install --pre`.
5. Approve `pypi` only after that check passes. Its request appears only after `publish-testpypi` succeeds. Check the commit SHA again before approving. If the check fails, reject the request: the version stays on TestPyPI only, and the fix ships as the next version.
6. Check the project page at https://pypi.org/p/openpickleball-engine, then run the same check against PyPI: the same command without `-i https://test.pypi.org/simple/`.

A version can be uploaded only once. If a publish fails after the upload, or the build was wrong, bump to the next version and tag again: the next dev number for a dev release (`0.3.0.dev2`), the next candidate number for a release candidate (`1.0.0rc3`), or the next patch version for a final release (`0.3.1`). Do not move an existing tag.

## The build tool lock

`.github/requirements/release.txt` pins every build tool with hashes. Do not edit it by hand. Dependabot (the `uv` ecosystem) is set to update it weekly. To regenerate it yourself, run the command in its header on Linux:

```bash
uv pip compile .github/requirements/release.in --generate-hashes --python-version 3.12 -o .github/requirements/release.txt
```

The command resolves for the platform it runs on, and the lock has no platform markers. Run on Linux, it matches the runner the workflows use; run on macOS or Windows, it drops Linux-only packages and the install on the runner fails. Recompile it if a workflow moves to another Python version or operating system. Dependabot may rewrite the command in the header in a slightly different form. That is fine as long as it keeps `--generate-hashes` and `--python-version 3.12`.
