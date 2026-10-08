# AGENTS.md

pickley is a CLI that installs python CLIs, and keeps them up-to-date. It runs on Python 3.10+.
The repo docs live in [`docs/`](./docs/index.md).

## Looking for

- **How to use pickley** → [`README.rst`](./README.rst). `docs/` is *internal*, users never see it.
- **What changed** → [`CHANGELOG.md`](./CHANGELOG.md).

## Working notes

- `tox.ini` is the hub. Every check is a tox environment and the workflows just call them, so
  reproduce any CI failure locally with the same `tox -e <env>`.
- Run tests with `.venv/bin/pytest` or through tox. Driving pytest from an unrelated interpreter
  breaks runez's `sys.argv` canonicalization and produces confusing failures.
- `src/pickley/bstrap.py` is also run standalone, by whatever system python is available (like
  macOS's `/usr/bin/python3`, which is older than 3.10). It must not use anything newer than that,
  nor import anything outside of the stdlib.
- Each type checker has a tox env named after it (`tox -e ty`, `tox -e pyright`, ...). ty, pyrefly,
  pyright, mypy and zuban run in CI and are kept at zero, basedpyright is a stricter second opinion
  (not in CI). No `# type: ignore` markers of any dialect: fix the code or the signature, or turn off
  a whole class of findings in config (scoped as narrowly as possible, with a comment saying why).
- ruff uses `select` so new ruff releases don't silently enable new rules.
