import os

from pickley import system
from pickley.context import CaptureOutput
from pickley.delivery import DeliveryMethodWrap
from pickley.uninstall import uninstall_existing


def test_wrapper(temp_base):
    repeater = os.path.join(temp_base, "repeat.sh")
    target = os.path.join(temp_base, system.PICKLEY)

    system.write_contents(repeater, "#!/bin/bash\n\necho :: $*\n")
    system.make_executable(repeater)

    # Actual wrapper
    d = DeliveryMethodWrap(system.PICKLEY)
    d.install(target, repeater)
    assert system.run_program(target, "auto-upgrade", "foo") == ":: auto-upgrade foo"
    assert system.run_program(target, "--debug", "auto-upgrade", "foo") == ":: --debug auto-upgrade foo"
    assert system.run_program(target, "settings", "-d") == ":: settings -d"

    # Verify that we're triggering background auto-upgrade as expected
    d.hook = "echo "
    d.bg = ""
    d.install(target, repeater)

    output = system.run_program(target, "settings", "-d")
    assert "nohup" in output
    assert "repeat.sh settings -d" in output

    output = system.run_program(target, "auto-upgrade", "foo")
    assert "nohup" not in output
    assert "repeat.sh auto-upgrade foo" in output

    output = system.run_program(target, "--debug", "auto-upgrade", "foo")
    assert "nohup" not in output
    assert "repeat.sh --debug auto-upgrade foo" in output

    system.delete_file(repeater)
    with CaptureOutput() as logged:
        system.run_program(target, "foo", fatal=False)
        assert "Please reinstall with" in logged

    assert os.path.exists(target)
    assert uninstall_existing(target, fatal=False) == 1
    assert not os.path.exists(target)
