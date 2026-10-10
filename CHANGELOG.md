# Changelog

## 4.7.0 (unreleased)

- pickley's code is now checked by multiple type checkers on every change
- Bug fixes:
  - `pickley uninstall --all` crashed when 2 or more packages were installed
  - Bootstrapping with `--package-manager=pip` could needlessly fall back to `virtualenv`
  - `pickley -n install` (dry-run) said "Would state:" twice
  - `pickley describe` showed "None" as package name when a package could not be resolved
  - Clearer error messages (instead of a stack trace) in a few edge cases
- Tests cover more real-world scenarios, coverage is back to 100%
