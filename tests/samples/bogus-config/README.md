# Sample config

This folder simulates a sample bogus pickley configuration.

Bogus things are simply ignored:
- `bogus.json` is an invalid json file (should be map, but contains an empty list)
- `custom.json` includes `bogus.json`, and points to a non-usable `python`


Exercised by  [test_config.py](../../test_config.py)
