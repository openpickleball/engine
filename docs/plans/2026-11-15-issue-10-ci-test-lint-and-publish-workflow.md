# Plan: CI: test, lint, and publish workflow (issue #10)

Risk tier: High. The change adds a release path that uploads to an external package index
using an OIDC `id-token: write` permission. That is security-sensitive and outward-facing,
and the real publish cannot be exercised before merge.

Domain skills: none applicable. The listed skills cover Django, FastAPI, React, Expo, and
browser work; this change is GitHub Actions YAML and a standard-library Python script.

## Status of the issue at the start of this work

Measured on `origin/main` at 942ad85:

- `.github/workflows/test.yml` already runs `ruff check .`, `ruff format --check .`,
  `mypy src`, and `pytest --cov` on Python 3.10, 3.11, 3.12, and 3.13 (the issue says
  3.11 to 3.13, but the package supports 3.10 and CI tests it; keep 3.10).
- `[tool.mypy] strict = true` is set and `mypy src` reports "Success: no issues found in
  3 source files" (fixed by issue #3).
- Every `uses:` in `.github/workflows/` is pinned to a commit SHA with a version comment.
- There is no release workflow, no GitHub environment, and no TestPyPI project.

So this work adds the release workflow. After review, it also changes `test.yml`: the
`package` job now installs and builds from the release lock file (criterion 7).

## Acceptance criteria

1. CI runs `ruff check .`, `ruff format --check .`, `mypy src`, and `pytest` on every
   supported Python version (3.10 to 3.13). Already true; verified, not changed.
2. `mypy src` passes under `strict = true`. Already true; verified, not changed.
3. A new `.github/workflows/release.yml` runs on a push of any tag matching `v*`. A
   `gate` job with only `contents: read` calls `./.github/workflows/test.yml` (which gains
   a `workflow_call:` trigger), so the full pull request matrix (3.10 to 3.13, including
   the criterion 8 test) and the `package` job rerun on the tagged commit. Its `build` job
   `needs: gate`, has only `contents: read`, checks out with `fetch-depth: 0` and
   `persist-credentials: false`, and first fails with a clear error unless
   `git merge-base --is-ancestor "$GITHUB_SHA^{commit}" origin/main` passes. This guards
   against mistakes, such as tagging an unmerged commit. It is not an enforcement
   boundary: the run uses the `release.yml` from the tagged commit, so a tag on a commit
   with an edited workflow skips both the gate and the check (see Risks). It then
   builds the sdist and the wheel once, and fails unless all of these pass:
   `python scripts/check_sdist.py dist/*.tar.gz`, `python scripts/check_release.py` (see 5),
   and `twine check --strict dist/*`. It uploads `dist/` as one artifact.
4. A separate `publish-testpypi` job `needs: build`, runs in the GitHub environment
   `testpypi`, has only `id-token: write`, downloads the artifact, and publishes it with
   `pypa/gh-action-pypi-publish` to `https://test.pypi.org/legacy/`. The workflow
   references no secret (`secrets.` appears nowhere in it) and does not check out the
   repository in the publish job.
5. `scripts/check_release.py <tag> <dist paths...>` (standard library only) exits 0 only
   when the tag is `v` followed by the version in both artifact file names, and `dist/`
   holds exactly one sdist `openpickleball_engine-<version>.tar.gz` and exactly one wheel
   `openpickleball_engine-<version>-py3-none-any.whl` and nothing else. Otherwise it exits 1
   and names each problem. A tag that is not in canonical form (such as `v0.3.0-dev1` for
   version `0.3.0.dev1`) fails. The workflow passes the tag through an environment
   variable, never by `${{ }}` interpolation inside `run:`.
6. Every `uses:` in `.github/workflows/` (new ones included) is pinned to a full 40-char
   commit SHA with the version in a trailing comment.
7. The release build installs its build backend and tools (`hatchling`, `build`,
   `twine`) with `pip install --only-binary :all: --require-hashes` from a committed,
   hash-pinned lock file (`.github/requirements/release.txt`, compiled from
   `.github/requirements/release.in`), and builds with `python -m build --no-isolation`,
   so no build dependency is fetched unpinned. `test.yml`'s `package` job uses the same
   install and build commands and adds `twine check --strict dist/*`, so a broken lock
   fails on a pull request, not at tag time. Both `test.yml` checkouts set
   `persist-credentials: false`. Dependabot covers that directory with the `uv`
   ecosystem, which regenerates the lock with uv and keeps `--generate-hashes` and
   `--python-version` from the header (the `pip` ecosystem would rerun pip-compile).
8. A test asserts `Config().version == open_engine.__version__`, so a release cannot tag
   a package whose config version lags.
9. Docs: a `CHANGELOG.md` entry under `[Unreleased]`; `docs/releasing.md` with the
   one-time setup a maintainer does by hand and the steps for each release; `CLAUDE.md`
   names the release workflow and the new script.
10. The issue's criterion "pushing a `v*.dev*` tag publishes to TestPyPI with no stored API
    token" can only be shown after merge, once a maintainer has done the one-time setup.
    The PR says so plainly and does not claim it.

## Files that change

- `.github/workflows/release.yml` (new).
- `.github/requirements/release.in` (new): `build`, `twine`, and `hatchling`, unpinned.
- `.github/requirements/release.txt` (new): generated with
  `uv pip compile .github/requirements/release.in --generate-hashes --python-version 3.12 -o .github/requirements/release.txt`.
  Never edited by hand.
- `.github/dependabot.yml`: add a `uv` entry for `directory: /.github/requirements`,
  weekly, grouped like the existing one.
- `.github/workflows/test.yml`: gains a `workflow_call:` trigger so `release.yml` can
  call it as its `gate` job. The `package` job installs from the lock with
  `--only-binary :all: --require-hashes`, builds with `--no-isolation`, and runs
  `twine check --strict`; both checkouts set `persist-credentials: false`.
- `scripts/check_release.py` (new).
- `tests/test_check_release.py` (new): loads the script the way
  `tests/test_check_sdist.py` loads `check_sdist.py`.
- A test for criterion 8, in the existing test module that covers `Config` (or
  `tests/test_version.py` if none fits).
- `CHANGELOG.md`, `CLAUDE.md`, `docs/releasing.md` (Phase 8, coordinator).

`scripts/` already ships in the sdist and is in `ALLOWED_TOP_LEVEL`, so the new script
needs no packaging change. `.github/` does not ship.

## Order of work

1. Red: `tests/test_check_release.py` with parametrized cases: valid release tag
   (`v0.2.0`), valid dev tag (`v0.3.0.dev1`), tag without `v`, tag whose version differs
   from the files, non-canonical tag (`v0.3.0-dev1` with `0.3.0.dev1` files), missing
   sdist, missing wheel, two wheels, an extra file, sdist and wheel versions that differ,
   no dist paths at all. Assert exit code and message. Run it: fails (no script).
   Commit the red test.
2. Green: `scripts/check_release.py`, with a module docstring in the style of
   `check_sdist.py` (usage, every exit condition). It inspects file names only
   (`Path(p).name`), not archive contents. Commit.
3. Red then green for criterion 8. If the test passes on first run (expected: both are
   `0.2.0`), it is a guard, not a defect fix; say so in the commit message and do not
   freeze it.
4. Lock file: write `release.in`, generate `release.txt` with the command above, add the
   Dependabot entry. Commit.
5. `release.yml`, exactly this shape (job and step names may be tuned):

   ```yaml
   name: Release

   on:
     push:
       tags: ["v*"]

   permissions: {}

   jobs:
     build:
       runs-on: ubuntu-latest
       permissions:
         contents: read
       steps:
         - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
           with:
             persist-credentials: false
         - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0
           with:
             python-version: "3.12"
         - name: Install build tools
           run: pip install --require-hashes -r .github/requirements/release.txt
         - name: Build
           run: python -m build
         - name: Check sdist contents
           run: python scripts/check_sdist.py dist/*.tar.gz
         - name: Check tag matches version
           env:
             TAG: ${{ github.ref_name }}
           run: python scripts/check_release.py "$TAG" dist/*
         - name: Check metadata
           run: twine check --strict dist/*
         - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
           with:
             name: dist
             path: dist/
             if-no-files-found: error

     publish-testpypi:
       needs: build
       runs-on: ubuntu-latest
       environment:
         name: testpypi
         url: https://test.pypi.org/p/openpickleball-engine
       permissions:
         id-token: write
       steps:
         - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2
           with:
             name: dist
             path: dist/
         - uses: pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086 # v1.14.3
           with:
             repository-url: https://test.pypi.org/legacy/
   ```

   After review, the shape changed as criteria 3 and 7 describe: a `gate` job calling
   `test.yml`, `fetch-depth: 0` and an ancestry check against `origin/main` in `build`,
   `--only-binary ':all:'` on the install, and `--no-isolation` on the build.

   The SHAs above were resolved with `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`
   on 2026-11-15. Re-resolve them the same way before committing and use what it returns.
   Commit.
6. Gate, then the live drive below.

## Risks

- **The real publish is unproven until after merge.** It needs a TestPyPI pending
  publisher and the `testpypi` GitHub environment, both done by a maintainer by hand. A
  typo in the environment name or workflow file name makes the OIDC exchange fail. The
  docs give the exact values; the first dev tag is the real test.
- **Tag pushes are a trust boundary.** Anyone who can push a `v*` tag can trigger a
  publish. In the workflow, the `gate` job reruns the tests on the tagged commit and the
  ancestry check refuses a commit that is not on `main`. Both catch mistakes only: GitHub
  runs the `release.yml` from the tagged commit, so someone who can push a tag can push
  one on a commit whose workflow drops them, and the trusted publisher (keyed to the
  workflow file name and environment) still accepts the upload. The controls that hold
  against that live outside this repo's files: a `v*` tag ruleset that limits who can
  create release tags, and a required reviewer on the `testpypi` environment who checks
  the tagged commit. Restricting the environment's deployment rule to `v*` tags stops
  branch runs from using it but does not stop a malicious `v*` tag. `docs/releasing.md`
  says which control does what, and says to create and protect the environment before
  the first tag push, because GitHub creates a missing environment with no protection.
- **Build tool supply chain.** Unpinned `pip install build twine` would let a compromised
  release of either tool alter the published artifact. Hash pinning closes that, and it
  now covers the build backend too: `hatchling` is in the lock and the build runs with
  `--no-isolation`, and `--only-binary :all:` stops a missing wheel from building from
  source with unpinned build dependencies. pip itself and the interpreter that
  `setup-python` installs remain unpinned. The cost is a lock file Dependabot must keep
  current.
- **Version drift.** A tag that does not match `__version__` would publish under the
  file's version, not the tag's. `check_release.py` blocks that; criterion 8 blocks
  `Config.version` lagging `__version__`.
- Riskiest step: step 5, because CI cannot run it on a pull request. Mitigated by
  actionlint and zizmor, and by running the build job's commands locally.

Chosen not to do: publishing to the real PyPI (a later issue); creating the
GitHub environment or the TestPyPI publisher from this run (external, done by a
maintainer).

## Proof

- Criteria 1, 2, 6: read `test.yml` and grep every `uses:` line in `.github/workflows/`
  for a 40-char SHA and a comment; the gate's `mypy src` output.
- Criterion 5: `tests/test_check_release.py`.
- Criterion 8: the version test.
- Criteria 3, 4, 7: `uvx --from actionlint-py actionlint` and `uvx zizmor .github/workflows/`
  clean on the new workflow; `grep -n 'secrets\.' .github/workflows/release.yml` empty.
- Live drive (High: changed path plus failure and edge paths), in a fresh scratch venv:
  `pip install --only-binary ':all:' --require-hashes -r .github/requirements/release.txt`
  succeeds; then from a clean `dist/`: `python -m build --no-isolation`, `check_sdist.py`,
  `check_release.py v0.2.0 dist/*` (exit 0), `check_release.py v0.2.1 dist/*` (exit 1),
  `check_release.py v0.2.0` with an extra file in `dist/` (exit 1),
  `twine check --strict dist/*` (passes). Ancestry: `git merge-base --is-ancestor` exits 0
  for the `origin/main` commit and 1 for this branch's unmerged `HEAD`.
- Measured on `origin/main` at 942ad85: `python -m build` produces
  `openpickleball_engine-0.2.0.tar.gz` and `openpickleball_engine-0.2.0-py3-none-any.whl`;
  `twine check --strict` on both reports PASSED. This fixes the file names
  `check_release.py` expects.
