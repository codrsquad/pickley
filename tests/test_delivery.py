import os

import runez

from pickley import system
from pickley.delivery import DeliveryMethodWrap
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
