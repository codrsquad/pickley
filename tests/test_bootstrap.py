import logging
import os
import sys
from unittest.mock import patch

import pytest
import runez

from pickley import __version__, bstrap

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

    with patch("pickley.bstrap.which", side_effect=lambda x: None if x == "pickley" else x):
        with patch("pickley.bstrap.os.path.expanduser", side_effect=mocked_expanduser):
            runez.write(".local/bin/pickley", "#!/bin/sh\necho 0.1")  # Pretend we have an old pickley
            runez.make_executable(".local/bin/pickley")

            monkeypatch.setenv("__PYVENV_LAUNCHER__", "foo")  # macOS's oddity
            cli.run("-n", main=bstrap.main)
            assert cli.succeeded
            assert "__PYVENV_LAUNCHER__" not in os.environ
            assert "Replacing older pickley 0.1" in cli.logged
            assert " -mvenv --clear " in cli.logged

            # Verify that we report base not writeable correctly
            cli.run("-n -b /dev/null/foo", main=bstrap.main)
            assert cli.failed
            assert "Make sure /dev/null/foo is writeable" in cli.logged

            # Simulate multiple base candidates given, verify that -c triggers PATH env var check
            cli.run("-n -cfoo", main=bstrap.main)
            assert cli.failed
            assert "Make sure .local/bin is in your PATH environment variable." in cli.logged

            # Simulate seeding
            sample_config = '"python-installations": "~/.pyenv/version/**"'
            monkeypatch.setenv("PATH", ".local/bin:%s" % os.environ["PATH"])
            cli.run("0.1", "-m", "my-mirror", "-c", f"{{{sample_config}}}", main=bstrap.main)
            assert "base: .local/bin" in cli.logged
            assert f"Seeding .local/bin/{dot_meta('config.json')} with " in cli.logged
            assert "Seeding .config/pip/pip.conf with my-mirror" in cli.logged
            assert "pickley version 0.1 is already installed" in cli.logged
            assert list(runez.readlines(".config/pip/pip.conf")) == ["[global]", "index-url = my-mirror"]
            assert list(runez.readlines(f".local/bin/{dot_meta('config.json')}")) == ["{", f"  {sample_config}", "}"]

            monkeypatch.setattr(bstrap, "DRYRUN", False)

            def mocked_run(program, *args, **__):
                if args[0] == "--version":
                    return "0.0"

                logging.info("Running %s %s" % (program, " ".join(args)))

            with patch("pickley.bstrap.run_program", side_effect=mocked_run):
                # Verify fallback to virtualenv
                cli.run("1.0", main=bstrap.main)
                assert cli.succeeded
                assert "-mvenv failed, falling back to virtualenv" in cli.logged
                assert "pip -q install pickley==1.0" in cli.logged
                assert "pickley base bootstrap-own-wrapper" in cli.logged

                # When pip available, don't use virtualenv
                pip = f".local/bin/{dot_meta('pickley-1.0/bin/pip3')}"
                runez.touch(pip)
                runez.make_executable(pip)
                cli.run("1.0", main=bstrap.main)
                assert cli.succeeded
                assert "virtualenv" not in cli.logged


def test_edge_cases(temp_folder, logged):
    bstrap.DRYRUN = False

    # For coverage
    assert bstrap.which("python3")
    assert bstrap.run_program(sys.executable, "--version") == 0

    logged.pop()
    with pytest.raises(SystemExit) as exc:
        bstrap.run_program(sys.executable, "--no-such-option")
    assert " exited with code" in str(exc)
    assert "python --no-such-option" in logged

    def mocked_run(program, *_, **__):
        return program

    with patch("pickley.bstrap.built_in_download", side_effect=Exception):  # urllib fails
        with patch("pickley.bstrap.run_program", side_effect=mocked_run):
            with patch("pickley.bstrap.which", side_effect=lambda x: "/bin/%s" % x if x in ("curl", "pickley") else None):
                assert bstrap.find_base(None) == "/bin"
                assert "Found existing /bin/pickley" in logged.pop()
                assert bstrap.download("test", "test") == "/bin/curl"

            with patch("pickley.bstrap.which", side_effect=lambda x: x if x == "wget" else None):
                assert bstrap.download("test", "test") == "wget"

            with patch("pickley.bstrap.which", return_value=None):
                with pytest.raises(SystemExit) as exc:
                    bstrap.download("test", "test")
                assert "No curl, nor wget" in str(exc)
