import os
from unittest.mock import MagicMock, patch

import pytest
import runez

from pickley import __version__, PackageSpec, PICKLEY, PickleyConfig
from pickley.delivery import auto_uninstall, DeliveryMethod
from pickley.package import PythonVenv


BREW_INSTALL = "/brew/install/bin"
BREW = os.path.join(BREW_INSTALL, "brew")
BREW_CELLAR = "/brew/install/Cellar"
BREW_INSTALLED = ["tox", "twine"]


def is_brew_link(path):
    return path and path.startswith(BREW_INSTALL)


def brew_realpath(path):
    """Simulate brew symlink for names in BREW_INSTALLED"""
    if path and path.startswith(BREW_INSTALL):
        basename = os.path.basename(path)
        if basename not in BREW_INSTALLED:
            return path

        return "{cellar}/{basename}/1.0/bin/{basename}".format(cellar=BREW_CELLAR, basename=basename)


def test_edge_cases(temp_folder, logged):
    cfg = PickleyConfig()
    pspec = PackageSpec(cfg, "mgit==1.0.0")
    venv = MagicMock(pspec=pspec, bin_path=lambda x: os.path.join("some-package/bin", x))
    entry_points = {"some-source": ""}
    cfg.set_base(".")
    d = DeliveryMethod()
    with pytest.raises(SystemExit):
        d.install(venv, entry_points)
    assert "Can't deliver some-source -> some-package/bin/some-source: source does not exist" in logged.pop()

    runez.touch("some-package/bin/some-source")
    with pytest.raises(SystemExit):
        d.install(venv, entry_points)
    assert "Failed to deliver" in logged.pop()


@patch("os.path.islink", side_effect=is_brew_link)
@patch("os.path.realpath", side_effect=brew_realpath)
def test_uninstall_brew(*_):
    with runez.CaptureOutput() as logged:
        with patch("runez.run", return_value=runez.program.RunResult(code=0)):
            # Simulate successful uninstall
            auto_uninstall(f"{BREW_INSTALL}/tox")
            assert "Auto-uninstalled brew formula 'tox'" in logged.pop()

        with patch("runez.run", return_value=runez.program.RunResult(code=1)):
            # Simulate failed uninstall
            with pytest.raises(SystemExit):
                auto_uninstall(f"{BREW_INSTALL}/twine")
            assert "brew uninstall twine' failed, please check" in logged.pop()

        with pytest.raises(SystemExit):
            auto_uninstall(f"{BREW_INSTALL}/wget")
        assert "Can't automatically uninstall" in logged.pop()


class SimulatedInstallation:
    def __init__(self, cfg, name, version):
        self.cfg = cfg
        self.name = name
        self.version = version
        self.entry_points = {name: name}
        self.pspec = PackageSpec(cfg, f"{name}=={version}")
        self.pspec.save_manifest(self.entry_points)
        folder = self.pspec.get_install_path(version)
        self.venv = PythonVenv(folder, self.pspec, create=False)
        venv_exe = os.path.join(folder, "bin", name)
        runez.write(venv_exe, f"#!/bin/bash\n\necho {version}\n")
        runez.make_executable(venv_exe)

    def check_wrap(self, wrap_method):
        impl = DeliveryMethod.delivery_method_by_name(wrap_method)
        impl.install(self.venv, self.entry_points)
        exe = runez.resolved_path(self.name)
        r = runez.run(exe, "--version", fatal=False)
        assert r.succeeded
        assert r.output == self.version

    def check_alternating(self, logged):
        self.check_wrap("wrap")
        assert f"Wrapped {self.name} -> " in logged.pop()
        self.check_wrap("symlink")
        assert f"Symlinked {self.name} -> " in logged.pop()
        self.check_wrap("wrap")
        assert f"Wrapped {self.name} -> " in logged.pop()


def test_wrapper(temp_cfg, logged):
    """Check that flip-flopping between symlink/wrapper works"""
    mgit = SimulatedInstallation(temp_cfg, "mgit", "1.0")
    mgit.check_alternating(logged)

    pickley = SimulatedInstallation(temp_cfg, PICKLEY, __version__)
    pickley.check_alternating(logged)
