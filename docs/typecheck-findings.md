# Type checking: v4.6.1 → v4.7

What v4.7 changed in response to type checker findings on v4.6.1.

| Checker | Version | v4.6.1 | v4.7 | Actionable | Bugs | Design | Minor |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ty | 0.0.85 | 88 | 0 | 88 | 0 | 6 | 5 |
| pyrefly | 1.3.2 | 111 | 0 | 111 | 1 | 8 | 8 |
| pyright | 1.1.414 | 120 | 0 | 120 | 2 | 8 | 9 |
| mypy | 2.4.0 | 47 | 0 | 47 | 0 | 4 | 4 |
| basedpyright | 1.40.2 | 2,740 | 2,384 | 449 | 2 | 8 | 10 |
| zuban | 0.10.0 | 72 | 0 | 72 | 2 | 8 | 8 |

- **v4.6.1**, **v4.7**: findings on that release's `src/`, `tests/` and `setup.py`, both checked
  with v4.7's settings (`tox.ini`, `pyproject.toml`).
- **Actionable**: v4.6.1 findings resolved by a change in v4.7.
- **Bugs**, **Design**, **Minor**: how many of the changes listed under [Bug fixes](#bug-fixes),
  [Design](#design) and [Minor](#minor) the checker spotted.
- basedpyright counts include its warnings: 131 errors + 2,609 warnings on v4.6.1, 8 + 2,376 on
  v4.7.
- v4.7's zeros need runez 5.10.1, which fixed some annotations pickley tripped on (see
  [Fixed in runez](#fixed-in-runez)).
- v4.6.1 had one `# type: PackageSpec` comment (on a `None` value), v4.7 has none.

## Bug fixes

A checker pointed at code that misbehaved.

- `uninstall --all` crashed with `TypeError` once 2 or more packages were installed (it sorted
  `PackageSpec` objects, which have no ordering) [pyrefly, pyright, basedpyright, zuban]
- Bootstrapping with `--package-manager=pip` fell back to virtualenv whenever `python -mvenv`
  printed something (its output was read as "needs virtualenv"), found by fixing `run_program()`'s
  mixed return type [pyright, basedpyright, zuban]

## Design

pickley's invariants are now stated in its types, and enforced where they used to be assumed.

- One base folder: `CFG.meta`, `.cache` and `.manifests` derive from `CFG.base`, which raises a
  clear error when not configured (instead of `None / ...` errors) [all six]
- A package's `auto_upgrade_spec` is always a `str`: a manifest without one (written by
  pickley < 4.4) is invalid, the package simply gets a fresh install (the "incomplete manifest"
  upgrade reason is gone) [all six]
- A manifest always has settings and entry points (uv's too) [all six]
- `PackageSpec.canonical_name`, `.target_version` and `.entrypoints` are never `None`: they abort
  with a clear message via the new `required_value()` (an `assert` with a nice error message),
  `check` and `auto-heal` look at `.problem` first, and a few `abort_if()` became
  `required_value()`, or redundant [ty, pyrefly, pyright, basedpyright, zuban]
- `target_installation_folder()` always returns a `Path` (uv has none, its callers handle
  it) [pyrefly, pyright, basedpyright, zuban]
- `VenvPackager.install()` returns `TrackedManifest | None` (`None` when installation failed in
  non-fatal mode) [ty, pyrefly, pyright, basedpyright, zuban]
- `DEFAULT_DELIVERY` and `DEFAULT_INSTALL_TIMEOUT`: the delivery method and install timeouts are
  always set [pyrefly, pyright, basedpyright, zuban]
- `Reporter` plugs into runez's tracer (`Traceable`) instead of monkey-patching a bound method,
  bstrap's `Reporter` is declared replaceable, and its `abort()` is `NoReturn` [all six]

## Fixed in runez

pickley tripped on these runez annotations, fixed in runez 5.10.1 rather than worked around.

- `to_int()` with a non-`None` default returns an `int` (and `to_float()` a `float`) [pyrefly,
  pyright, basedpyright], which also uncovered a runez bug: `to_float("1.5", default=0)`
  returned `0.0`
- `json_sanitized()` of a dict is a dict [basedpyright, zuban]
- `Version.mm` and `.main` are a `str` (empty when not applicable) [basedpyright, zuban]
- `ClickRunner.context_wrapper` accepts any context manager class [ty]

## Removed

A checker flagged code that turned out not to be needed.

- `PythonDepot.use_path = False` in tests: runez dropped that setting long ago, the line did
  nothing [ty, pyright, mypy, basedpyright, zuban]

## Minor

The checker was right, the fix changes little in practice.

- Attributes that can be `None` say so (`PipMetadata`, `ResolvedPackage`, `Tracked*`,
  ...) [all six]
- `package` command: one type per attribute (`dist` stays a `str` until resolved, requirement
  files are `Path`s) [all six]
- bstrap passes `str`, not `Path`, for the bootstrap base argument and `VIRTUAL_ENV` [all six]
- `ResolvedPackage.logger` and `PythonVenv.logger` typed as runez's `LoggerSpec` [ty, pyrefly,
  pyright, basedpyright, zuban]
- `uninstall` uses the manifest it checks for, instead of checking the installed version and then
  assuming a manifest [pyrefly, pyright, basedpyright]
- `RunSetup(command)` defaults `package` to `command`, as its docstring said [pyrefly, pyright,
  basedpyright]
- `pip show` reporting no location aborts with a clear message, dev mode no longer builds
  `-e None` [pyright, basedpyright, zuban]
- `find_symbolic_invoker()` always returns a `str` [basedpyright, zuban]
- `setup.py`: `setup_requires` is a list [pyrefly, pyright, mypy, basedpyright, zuban]
- Tests: optional values checked before use, `TemporaryBase` uses what `__enter__()`
  returns [ty, pyrefly, pyright, basedpyright, zuban]

## Not from a type checker

Found while bringing test coverage back to 100%.

- Dry-run `install` printed `Would state: Would state: Installed ...`
- `describe` showed `None` as package name when resolution failed
- A test assertion was always true (it was missing its `in cli.logged`)
