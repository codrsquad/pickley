# pickley internal docs

These are the *inside view* docs for the pickley repo: the "why" behind choices that aren't obvious
from reading the code. They are for people (and coding agents) working **on** pickley. Users want
the [README](../README.rst) instead.

`tox.ini` is the hub: every check a developer or CI can run is a tox environment, and the GitHub
workflows do nothing but call into them.

## Sections

- [Type checking in v4.7.0](./typecheck-v4.7.0.md) — what introducing the type checkers found and
  fixed.
- [Changelog](../CHANGELOG.md) — release notes.
