import os

import pytest
import runez
from mock import MagicMock, patch

from pickley import PackageSpec, PickleyConfig
from pickley.delivery import DeliveryMethod, ensure_safe_to_replace


BREW_INSTALL = "/brew/install/bin"
BREW = os.path.join(BREW_INSTALL, "brew")
BREW_CELLAR = "/brew/install/Cellar"
BREW_INSTALLED = ["tox", "twine"]


def is_brew_link(path):
    return path and path.startswith(BREW_INSTALL)


def brew_exists(path):
    """Pretend any file under BREW_INSTALL exists"""
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
        return runez.program.RunResult("OK", "", 0)

    return runez.program.RunResult("", "something failed", 1)


@pytest.fixture
def brew():
    cfg = PickleyConfig()
    cfg.set_base(".")
    with patch("os.path.exists", side_effect=brew_exists):
        with patch("os.path.islink", side_effect=is_brew_link):
            with patch("os.path.realpath", side_effect=brew_realpath):
                with patch("runez.run", side_effect=brew_run_program):
                    yield cfg


def test_edge_cases(temp_folder, logged):
    venv = MagicMock(bin_path=lambda x: os.path.join("mypkg/bin", x))
    entry_points = {"some-source": ""}
    cfg = PickleyConfig()
    cfg.set_base(".")
    pspec = PackageSpec(cfg, "mgit==1.0.0")
    d = DeliveryMethod()
    with pytest.raises(SystemExit):
        d.install(pspec, venv, entry_points)
    assert "Can't deliver some-source -> mypkg/bin/some-source: source does not exist" in logged.pop()

    runez.touch("mypkg/bin/some-source")
    with pytest.raises(SystemExit):
        d.install(pspec, venv, entry_points)
    assert "Failed to deliver" in logged.pop()


def test_uninstall(temp_folder, logged):
    cfg = PickleyConfig()
    cfg.set_base(".")
    with pytest.raises(SystemExit):
        ensure_safe_to_replace(cfg, "/dev/null")  # Can't uninstall unknown locations
    assert "Can't automatically uninstall" in logged.pop()

    runez.touch(".pickley/foo/bin/foo")
    runez.symlink(".pickley/foo/bin/foo", "foo")
    ensure_safe_to_replace(cfg, "foo")

    runez.write("bar", "# pickley wrapper")
    ensure_safe_to_replace(cfg, "bar")

    runez.touch("empty")
    ensure_safe_to_replace(cfg, "empty")


def test_uninstall_brew(temp_folder, brew, logged):
    # Simulate successful uninstall
    ensure_safe_to_replace(brew, "%s/tox" % BREW_INSTALL)
    assert "Auto-uninstalled brew formula 'tox'" in logged.pop()

    # Simulate failed uninstall
    with pytest.raises(SystemExit):
        ensure_safe_to_replace(brew, "%s/twine" % BREW_INSTALL)
    assert "brew uninstall twine' failed, please check" in logged.pop()

    with pytest.raises(SystemExit):
        ensure_safe_to_replace(brew, "%s/wget" % BREW_INSTALL)
    assert "Can't automatically uninstall" in logged.pop()
