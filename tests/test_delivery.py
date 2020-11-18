import os

import pytest
import runez
from mock import MagicMock, patch

from pickley import PackageSpec, PickleyConfig
from pickley.delivery import auto_uninstall, DeliveryMethod


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


def test_edge_cases(temp_folder, logged):
    venv = MagicMock(bin_path=lambda x: os.path.join("some-package/bin", x))
    entry_points = {"some-source": ""}
    cfg = PickleyConfig()
    cfg.set_base(".")
    pspec = PackageSpec(cfg, "mgit", "1.0.0")
    d = DeliveryMethod()
    with pytest.raises(SystemExit):
        d.install(pspec, venv, entry_points)
    assert "Can't deliver some-source -> some-package/bin/some-source: source does not exist" in logged.pop()

    runez.touch("some-package/bin/some-source")
    with pytest.raises(SystemExit):
        d.install(pspec, venv, entry_points)
    assert "Failed to deliver" in logged.pop()


@patch("os.path.exists", side_effect=brew_exists)
@patch("os.path.islink", side_effect=is_brew_link)
@patch("os.path.realpath", side_effect=brew_realpath)
def test_uninstall_brew(*_):
    with runez.CaptureOutput() as logged:
        with patch("runez.run", return_value=runez.program.RunResult(code=0)):
            # Simulate successful uninstall
            auto_uninstall("%s/tox" % BREW_INSTALL)
            assert "Auto-uninstalled brew formula 'tox'" in logged.pop()

        with patch("runez.run", return_value=runez.program.RunResult(code=1)):
            # Simulate failed uninstall
            with pytest.raises(SystemExit):
                auto_uninstall("%s/twine" % BREW_INSTALL)
            assert "brew uninstall twine' failed, please check" in logged.pop()

        with pytest.raises(SystemExit):
            auto_uninstall("%s/wget" % BREW_INSTALL)
        assert "Can't automatically uninstall" in logged.pop()
