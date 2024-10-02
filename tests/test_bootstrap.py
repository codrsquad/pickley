import json
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
    runez.touch(".pickley/config.json", logger=None)
    cli.run("-n base bootstrap-own-wrapper")
    assert cli.succeeded
    assert f"Would move .pickley/config.json -> {dot_meta('config.json')}" in cli.logged
    assert f"Would save {dot_meta('pickley.manifest.json')}" in cli.logged
    assert "Would delete .pickley" in cli.logged
    pickley_path = dot_meta(f"pickley-{__version__}/bin/pickley")
    assert f"Would run: {pickley_path} auto-heal" in cli.logged

    # Verify that we report base not writeable correctly
    cli.run("-n --check-path", main=bstrap.main)
    assert cli.failed
    assert "Make sure '.local/bin' is writeable" in cli.logged

    runez.ensure_folder(".local/bin", logger=None)
    cli.run("-n --package-manager foo", main=bstrap.main)
    assert cli.failed
    assert "Unsupported package manager 'foo'" in cli.logged

    if bstrap.USE_UV:
        runez.touch(".pk/.uv/bin/uv", logger=None)
        runez.make_executable(".pk/.uv/bin/uv", logger=None)
        cli.run("-n base bootstrap-own-wrapper")
        assert cli.succeeded
        assert "Would move .pk/.uv -> .pk/uv-" in cli.logged
        assert "Would wrap uv -> .pk/uv-" in cli.logged
        assert "Would delete .pk/.uv" in cli.logged

        cli.run("-n", main=bstrap.main)
        assert cli.succeeded
        assert "uv -q venv " in cli.logged

    else:
        cli.run("-n", main=bstrap.main)
        assert cli.succeeded
        assert " -mvenv --clear " in cli.logged
        assert "pickley base bootstrap-own-wrapper" in cli.logged
        return  # The rest of the test is UV specific

    with patch("pickley.bstrap.os.path.expanduser", side_effect=mocked_expanduser):
        runez.write(".local/bin/pickley", "#!/bin/sh\necho 0.1", logger=None)  # Pretend we have an old pickley
        runez.make_executable(".local/bin/pickley", logger=None)

        runez.write(".local/bin/uv", "#!/bin/sh\necho uv 0.0.1", logger=None)  # Pretend we have uv already
        runez.make_executable(".local/bin/uv", logger=None)

        with patch("pickley.bstrap.http_get", return_value='{"info":{"version":"4.3.0"}}'):
            cli.run("-n -mhttps://my-company.net/some-path/simple/", main=bstrap.main)
            assert cli.succeeded
            assert "Querying https://my-company.net/some-path/pypi/pickley/json" in cli.logged
            assert "uv -q pip install pickley==4.3.0" in cli.logged

        cli.run("-n", main=bstrap.main)
        assert cli.succeeded
        assert "Replacing older pickley v0.1" in cli.logged

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
        r = runez.run(uv_path, "venv", ".venv", fatal=False, logger=None)
        assert r.succeeded

        # Verify that a bogus uv config file fails the run...
        runez.write(uv_config, "[pip]\nindex-url = http://foo", logger=None)
        r = runez.run(uv_path, "venv", ".venv", fatal=False, logger=None)
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


def test_download_uv(temp_cfg, monkeypatch):
    assert bstrap.uv_url(None)

    monkeypatch.setattr(bstrap, "DRYRUN", True)
    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    tmp_base = runez.to_path(temp_cfg.base.path)
    assert bstrap.find_uv(tmp_base) == tmp_base / ".pk/.uv/bin/uv"

    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    runez.write("sample-uv", "#!/bin/sh\necho uv 0.0.1", logger=None)
    runez.make_executable("sample-uv", logger=None)
    runez.symlink("sample-uv", "uv", logger=None)
    assert bstrap.find_uv(tmp_base) == tmp_base / "uv"

    # Simulate bad uv download
    monkeypatch.setattr(bstrap, "_UV_PATH", None)
    runez.write("sample-uv", "#!/bin/sh\nexit 1", logger=None)
    assert bstrap.find_uv(tmp_base) == tmp_base / ".pk/.uv/bin/uv"


def test_edge_cases(temp_cfg, logged):
    # For coverage
    assert bstrap.which("python3")
    assert bstrap.run_program(sys.executable, "--version") == 0
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


def test_failure(cli, monkeypatch):
    monkeypatch.setattr(bstrap, "DRYRUN", False)
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

    def mocked_run(program, *args, **__):
        if program != "wget":
            print(f"Running {program} {runez.joined(args)}")
            return

        with open(args[2], "w") as fh:
            json.dump({"info": {"version": "1.0"}}, fh)

    # Simulate a py3.6 ssl issue, fallback to using curl or wget to figure out latest pickley version
    with patch("pickley.bstrap.Request", side_effect=URLError("ssl issue")):
        with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "wget" else None):
            with patch("pickley.bstrap.run_program", side_effect=mocked_run):
                cli.run("-n --base .", main=bstrap.main)
                assert cli.succeeded
                assert "pip install pickley==1.0"


def test_pip_conf(temp_cfg, logged):
    assert bstrap.globally_configured_pypi_mirror([]) == (bstrap.DEFAULT_MIRROR, None)

    # Verify that we try reading all stated pip.conf files in order, and user the value from the first valid one
    runez.write("a", "ouch, this is not a valid config file", logger=False)  # Invalid file
    runez.write("b", "[global]\nindex-url = /", logger=False)  # Valid, but mirror not actually configured
    runez.write("c", "[global]\nindex-url = https://example.com/simple//", logger=False)
    runez.write("d", "[global]\nindex-url = https://example.com/pypi2", logger=False)  # Not needed, previous one wins
    assert bstrap.globally_configured_pypi_mirror(["no-such-file.conf", "a", "b", "c", "d"]) == ("https://example.com/simple", "c")

    # Keep chatter to a minimum
    assert "no-such-file.conf" not in logged  # No chatter about non-existing files
    assert "Could not read 'a'" in logged.pop()
