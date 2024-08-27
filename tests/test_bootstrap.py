import logging
import os
import sys
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
import runez

from pickley import __version__, bstrap
from pickley.cli import CFG

from .conftest import dot_meta


def mocked_expanduser(path):
    if path and path.startswith("~/"):
        path = path[2:]

    return path


def test_bootstrap(cli, monkeypatch):
    monkeypatch.setattr(bstrap, "DRYRUN", False)
    runez.touch(".pickley/config.json")
    cli.run("-n base bootstrap-own-wrapper")
    assert cli.succeeded
    assert f"Would move .pickley/config.json -> {dot_meta('config.json')}" in cli.logged
    assert f"Would save {dot_meta('pickley.manifest.json')}" in cli.logged
    assert "Would delete .pickley" in cli.logged
    pickley_path = dot_meta(f"pickley-{__version__}/bin/pickley")
    assert f"Would run: {pickley_path} auto-heal" in cli.logged

    if sys.version_info[:2] >= (3, 8):
        runez.touch(".pk/.uv/bin/uv")
        runez.make_executable(".pk/.uv/bin/uv")
        cli.run("-n base bootstrap-own-wrapper")
        assert cli.succeeded
        assert "Would move .pk/.uv -> .pk/uv-" in cli.logged
        assert "Would wrap uv -> .pk/uv-" in cli.logged

    with patch("pickley.bstrap.os.path.expanduser", side_effect=mocked_expanduser):
        runez.write(".local/bin/pickley", "#!/bin/sh\necho 0.1")  # Pretend we have an old pickley
        runez.make_executable(".local/bin/pickley")

        if sys.version_info[:2] >= (3, 8):
            runez.write(".local/bin/uv", "#!/bin/sh\necho uv 0.0.1")  # Pretend we have uv already
            runez.make_executable(".local/bin/uv")
            # Temporary: verify pip default for v4.1
            cli.run("-n 4.1", main=bstrap.main)
            assert cli.succeeded
            assert "Would run: .local/bin/.pk/pickley-4.1/bin/pip -q install pickley==4.1" in cli.logged

            # Temporary: verify uv default for v4.2+
            cli.run("-n 4.3", main=bstrap.main)
            assert cli.succeeded
            assert "Would run: .local/bin/uv -q venv -p " in cli.logged

        monkeypatch.setenv("__PYVENV_LAUNCHER__", "foo")  # macOS's oddity
        cli.run("-n", main=bstrap.main)
        assert cli.succeeded
        assert "__PYVENV_LAUNCHER__" not in os.environ
        assert "Replacing older pickley v0.1" in cli.logged

        cli.run("-n --package-manager foo", main=bstrap.main)
        assert cli.failed
        assert "Unsupported package manager 'foo'" in cli.logged

        cli.run("-n --package-manager uv", main=bstrap.main)
        assert cli.succeeded
        assert "uv -q venv " in cli.logged

        cli.run("-n --package-manager pip", main=bstrap.main)
        assert cli.succeeded
        assert "Replacing older pickley v0.1" in cli.logged
        assert " -mvenv --clear " in cli.logged

        # Verify that we report base not writeable correctly
        cli.run("-n -b /dev/null/foo --check-path", main=bstrap.main)
        assert cli.failed
        assert "Make sure '/dev/null/foo' is writeable" in cli.logged

        cli.run("-n -cfoo", main=bstrap.main)
        assert cli.failed
        assert "--config must be a serialized json object" in cli.logged

        # Simulate multiple base candidates given, verify that -c triggers PATH env var check
        cli.run("-n --check-path", main=bstrap.main)
        assert cli.failed
        assert "Make sure '.local/bin' is in your PATH environment variable." in cli.logged

        # Simulate seeding
        uv_config = ".config/uv/uv.toml"
        sample_config = '"python-installations": "~/.pyenv/version/**"'
        monkeypatch.setenv("PATH", ".local/bin:%s" % os.environ["PATH"])
        mirror = "https://pypi.org/simple"
        cli.run("0.1", "-m", mirror, "-c", f"{{{sample_config}}}", main=bstrap.main)
        assert "base: .local/bin" in cli.logged
        assert f"Seeding .local/bin/{dot_meta('config.json')} with " in cli.logged
        assert f"Seeding .config/pip/pip.conf with {mirror}" in cli.logged
        assert f"Seeding {uv_config} with {mirror}" in cli.logged
        assert "pickley version 0.1 is already installed" in cli.logged
        assert list(runez.readlines(".config/pip/pip.conf")) == ["[global]", f"index-url = {mirror}"]
        assert list(runez.readlines(uv_config)) == ["[pip]", f'index-url = "{mirror}"']
        assert list(runez.readlines(f".local/bin/{dot_meta('config.json')}")) == ["{", f"  {sample_config}", "}"]

        # Now verify that uv works with the seeded file
        monkeypatch.setenv("UV_CONFIG_FILE", uv_config)
        uv_path = CFG.find_uv()
        r = runez.run(uv_path, "venv", ".venv", fatal=False)
        assert r.succeeded

        # Verify that a bogus uv config file fails the run...
        runez.write(uv_config, "[pip]\nindex-url = http://foo")
        r = runez.run(uv_path, "venv", ".venv", fatal=False)
        assert r.failed
        assert "Failed to parse" in r.error

        # Ensure failing to seed uv/pip config files is not fatal
        runez.delete(".config", logger=None)
        runez.touch(".config/pip", logger=None)
        runez.touch(".config/uv", logger=None)
        cli.run("0.1", "-m", "my-mirror", main=bstrap.main)
        assert cli.succeeded
        assert "Seeding ~/.config/pip/pip.conf failed" in cli.logged
        assert "Seeding ~/.config/uv/uv.toml failed" in cli.logged

        monkeypatch.setattr(bstrap, "DRYRUN", False)

        def mocked_run(program, *args, **__):
            if args[0] == "--version":
                return "0.0"

            if "-mvenv" in args:
                return 1

            logging.info("Running %s %s", program, " ".join(str(x) for x in args))

        with patch("pickley.bstrap.run_program", side_effect=mocked_run):
            # Verify fallback to virtualenv
            cli.run("--package-manager=pip 1.0", main=bstrap.main)
            assert cli.succeeded
            assert "-mvenv failed, falling back to virtualenv" in cli.logged
            assert "python .local/bin/.pk/.cache/virtualenv-" in cli.logged
            assert "pip -q install pickley==1.0" in cli.logged
            assert "pickley base bootstrap-own-wrapper" in cli.logged


def test_edge_cases(temp_cfg, logged):
    bstrap.DRYRUN = False

    # For coverage
    with pytest.raises(SystemExit) as exc:
        bstrap.run_program(sys.executable, "--no-such-option")
    assert " exited with code" in str(exc)
    assert "python --no-such-option" in logged.pop()

    def mocked_run(program, *_, **__):
        return program

    with patch("pickley.bstrap.built_in_download", side_effect=Exception):  # urllib fails
        with patch("pickley.bstrap.Request", side_effect=Exception):
            with pytest.raises(SystemExit, match="Failed to fetch http://foo"):
                bstrap.http_get("http://foo")

        with patch("pickley.bstrap.run_program", side_effect=mocked_run):
            with patch("pickley.bstrap.which", side_effect=lambda x: "/bin/%s" % x if x in ("curl", "pickley") else None):
                assert bstrap.download("test", "test") == "/bin/curl"

            with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "wget" else None):
                assert bstrap.download("test", "test") == "wget"

            with patch("pickley.bstrap.which", return_value=None):
                with pytest.raises(SystemExit) as exc:
                    bstrap.download("test", "test")
                assert "No `curl` nor `wget`" in str(exc)


def test_failure(cli):
    with patch("pickley.bstrap.Request", side_effect=HTTPError("url", 404, "msg", None, None)):
        cli.run("--base .", main=bstrap.main)
        assert cli.failed
        assert "Failed to determine latest pickley version" in cli.logged

    with patch("pickley.bstrap.Request", side_effect=HTTPError("url", 500, "msg", None, None)):
        cli.run("--base .", main=bstrap.main)
        assert cli.failed
        assert "Failed to fetch https://pypi.org/pypi/pickley/json: HTTP Error 500: msg" in cli.logged

    with patch("pickley.bstrap.Request", side_effect=URLError("foo")):
        cli.run("--base .", main=bstrap.main)
        assert cli.failed
        assert "Failed to fetch https://pypi.org/pypi/pickley/json: <urlopen error foo>" in cli.logged
