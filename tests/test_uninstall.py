import os

from mock import patch

from pickley.uninstall import BREW_CELLAR, uninstall_existing, USR_LOCAL_BIN

from .conftest import verify_abort


BREW_INSTALLED = ["tox", "twine"]


def test_cant_uninstall():
    # no-op edge case
    uninstall_existing(None)

    # Can't uninstall unknown locations
    assert "Please uninstall /dev/null first" in verify_abort(uninstall_existing, "/dev/null")


def brew_exists(path):
    """Pretend any file under /usr/local/bin exists"""
    return path and path.startswith(USR_LOCAL_BIN)


def brew_realpath(path):
    """Simulate brew symlink for names in BREW_INSTALLED"""
    if path and path.startswith(USR_LOCAL_BIN):
        basename = os.path.basename(path)
        if basename not in BREW_INSTALLED:
            return path

        return "{cellar}/{basename}/1.0/bin/{basename}".format(cellar=BREW_CELLAR, basename=basename)

    return path


def brew_run_program(*args, **kwargs):
    """Simulate success for uninstall tox, failure otherwise"""
    if args[1] == "uninstall" and args[3] == "tox":
        return "OK"
    return None


@patch("os.path.exists", side_effect=brew_exists)
@patch("os.path.realpath", side_effect=brew_realpath)
@patch("pickley.system.run_program", side_effect=brew_run_program)
def test_uninstall_brew(*_):
    # Simulate successful uninstall
    uninstall_existing("/usr/local/bin/tox")

    # Simulate failed uninstall
    assert "Please uninstall /usr/local/bin/twine first" in verify_abort(uninstall_existing, "/usr/local/bin/twine")

    # Simulate not installed by brew
    assert "Please uninstall /usr/local/bin/wget first" in verify_abort(uninstall_existing, "/usr/local/bin/wget")
