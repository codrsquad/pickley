import os

from mock import patch

from pickley.uninstall import brew_uninstall, find_brew_name, uninstall_existing


BREW_INSTALL = "/brew/install/bin"
BREW = os.path.join(BREW_INSTALL, "brew")
BREW_CELLAR = "/brew/install/Cellar"
BREW_INSTALLED = ["tox", "twine"]


def is_brew_link(path):
    return path and path.startswith(BREW_INSTALL)


def brew_exists(path):
    """Pretend any file under BREW_INSTALL exists"""
    if path == BREW:
        return False
    return path and path.startswith(BREW_INSTALL)


def brew_realpath(path):
    """Simulate brew symlink for names in BREW_INSTALLED"""
    if path and path.startswith(BREW_INSTALL):
        basename = os.path.basename(path)
        if basename not in BREW_INSTALLED:
            return path

        return "{cellar}/{basename}/1.0/bin/{basename}".format(cellar=BREW_CELLAR, basename=basename)

    return path


def brew_run_program(*args, **kwargs):
    """Simulate success for uninstall tox, failure otherwise"""
    if args[1] == "uninstall" and args[3] == "tox":
        return "OK"
    return False


def test_cant_uninstall():
    # no-op edge case
    assert uninstall_existing(None, fatal=False) == 0

    # Can't uninstall unknown locations
    assert uninstall_existing("/dev/null", fatal=False) == -1

    assert brew_uninstall("", fatal=False) == -1


@patch("os.path.islink", side_effect=is_brew_link)
@patch("os.path.realpath", side_effect=brew_realpath)
@patch("runez.is_executable", return_value=False)
def test_find_brew_edge_case(*_):
    assert find_brew_name("%s/tox" % BREW_INSTALL) == (None, None)


@patch("os.path.exists", side_effect=brew_exists)
@patch("os.path.islink", side_effect=is_brew_link)
@patch("os.path.realpath", side_effect=brew_realpath)
@patch("runez.is_executable", side_effect=is_brew_link)
@patch("runez.run", side_effect=brew_run_program)
def test_uninstall_brew(*_):
    # Simulate successful uninstall
    assert uninstall_existing("%s/tox" % BREW_INSTALL, fatal=False) == 1

    # Simulate failed uninstall
    assert uninstall_existing("%s/twine" % BREW_INSTALL, fatal=False) == -1
    assert uninstall_existing("%s/wget" % BREW_INSTALL, fatal=False) == -1
