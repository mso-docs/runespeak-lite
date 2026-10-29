# Alpha release procedure

Commits alone stay local. After connecting this repository to GitHub, pushes and pull requests run checks and build artifacts; no release is published automatically.

## First setup

Create/connect the intended GitHub repository and push this project. No remote is configured in this checkout yet. Workflows use the built-in GitHub token; no publishing token or PyPI account is needed. The current application version is 0.1.0a1.

## Every pushed commit

The Alpha checks and builds workflow runs Linux tests under Xvfb, exercises document/OCR conversion and browser handlers, builds Python packages and a clean source bundle, checks contents/checksums, and uploads the assets as a workflow artifact for 14 days. It does not download speech models or require sound hardware. Real inference remains an opt-in local smoke test.

## Prepare an unpublished release

1. Set the next alpha version in pyproject.toml, run uv lock, update release notes, and commit/push.
2. Create and push the matching tag, for example:
   ~~~bash
   git tag v0.1.0a1
   git push origin v0.1.0a1
   ~~~
3. In GitHub Actions choose Prepare draft alpha release, then Run workflow. Select that same tag in the ref selector and enter v0.1.0a1 as the tag input. The workflow refuses a version/tag/commit mismatch.
4. CI reruns and attaches validated assets to an unpublished draft prerelease. Review it under Releases. Publication is a separate manual action in GitHub's release editor.

Pushing a tag alone runs checks/builds and does not create or publish a release. The draft action intentionally fails if that release already exists, rather than silently modifying it.

## Build locally

~~~bash
uv sync --frozen
uv build
python3 scripts/build-deb.py
python3 scripts/package-release.py
python3 scripts/verify-release.py
~~~

Use a clean dist directory for each version. Review docs/alpha-release-notes.md before preparing a draft. The application has not yet declared an open-source license; choose the intended project license before public distribution. Model/runtime/voice licenses remain separate from the application.

The `.deb` targets x86-64 Ubuntu 24.04+ / Debian 12+. It installs the launcher, icons and packaged wheel, then uses a per-user, per-version Python environment with hash-checked dependencies exported from uv.lock. First launch requires internet and shows setup progress. Model downloads remain separate. Building requires `dpkg-deb` and `uv`; no root or system installation is needed to build. Windows packaging is deferred.
