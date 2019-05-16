import logging
import os

import runez
from mock import patch

from pickley import system
from pickley.delivery import _relocator, DeliveryMethodWrap, relocate_venv
from pickley.uninstall import uninstall_existing


def test_wrapper(temp_base):
    repeater = os.path.join(temp_base, "repeat.sh")
    target = os.path.join(temp_base, system.PICKLEY)

    runez.write(repeater, "#!/bin/bash\n\necho :: $*\n")
    runez.make_executable(repeater)

    # Actual wrapper
    d = DeliveryMethodWrap(system.PackageSpec(system.PICKLEY))
    d.install(target, repeater)
    assert runez.run(target, "auto-upgrade", "foo") == ":: auto-upgrade foo"
    assert runez.run(target, "--debug", "auto-upgrade", "foo") == ":: --debug auto-upgrade foo"
    assert runez.run(target, "settings", "-d") == ":: settings -d"

    # Verify that we're triggering background auto-upgrade as expected
    d.hook = "echo "
    d.bg = ""
    d.install(target, repeater)

    output = runez.run(target, "settings", "-d")
    assert "nohup" in output
    assert "repeat.sh settings -d" in output

    output = runez.run(target, "auto-upgrade", "foo")
    assert "nohup" not in output
    assert "repeat.sh auto-upgrade foo" in output

    output = runez.run(target, "--debug", "auto-upgrade", "foo")
    assert "nohup" not in output
    assert "repeat.sh --debug auto-upgrade foo" in output

    runez.delete(repeater)
    with runez.CaptureOutput() as logged:
        runez.run(target, "foo", fatal=False)
        assert "Please reinstall with" in logged

    assert os.path.exists(target)
    assert uninstall_existing(target, fatal=False) == 1
    assert not os.path.exists(target)


def test_relocate_venv(temp_base):
    with patch("pickley.delivery.relocate_venv", return_value=-1):
        assert _relocator("source", "destination") == " (relocation failed)"

    with runez.CaptureOutput() as logged:
        original = "line 1: source\nline 2\n"
        runez.write("foo/bar/bin/baz", original, logger=logging.debug)
        runez.write("foo/bar/bin/empty", "", logger=logging.debug)
        runez.write("foo/bar/bin/python", "", logger=logging.debug)
        runez.make_executable("foo/bar/bin/baz")
        runez.make_executable("foo/bar/bin/empty")
        runez.make_executable("foo/bar/bin/python")
        assert "Created" in logged.pop()

        # Simulate already seen
        expected = ["line 1: source\n", "line 2\n"]
        assert relocate_venv("foo", "source", "dest", fatal=False, _seen={"foo"}) == 0
        assert runez.get_lines("foo/bar/bin/baz") == expected
        assert not logged

        # Simulate failure to write
        with patch("runez.write", return_value=-1):
            assert relocate_venv("foo", "source", "dest", fatal=False) == -1
        assert runez.get_lines("foo/bar/bin/baz") == expected
        assert not logged

        # Simulate effective relocation, by folder
        expected = ["line 1: dest\n", "line 2\n"]
        assert relocate_venv("foo", "source", "dest", fatal=False) == 1
        assert runez.get_lines("foo/bar/bin/baz") == expected
        assert not logged

        # Second relocation is a no-op
        assert relocate_venv("foo", "source", "dest", fatal=False) == 0

        # Test relocating a single file
        runez.write("foo/bar/bin/baz", original, logger=logging.debug)
        assert relocate_venv("foo/bar/bin/baz", "source", "dest", fatal=False) == 1
        assert runez.get_lines("foo/bar/bin/baz") == expected
