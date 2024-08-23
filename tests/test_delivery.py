import os
from unittest.mock import MagicMock

import pytest
import runez

from pickley import __version__, PackageSpec, PICKLEY
from pickley.delivery import DeliveryMethod
from pickley.package import PythonVenv

BREW_INSTALL = "/brew/install/bin"
BREW = os.path.join(BREW_INSTALL, "brew")
BREW_CELLAR = "/brew/install/Cellar"
BREW_INSTALLED = ["tox", "twine"]


def test_edge_cases(temp_cfg, logged):
    pspec = PackageSpec(temp_cfg, "mgit==1.0.0")
    venv = MagicMock(folder=pspec.venv_path(pspec.given_version), pspec=pspec)
    entry_points = {"some-source": ""}
    d = DeliveryMethod()
    with pytest.raises(SystemExit):
        d.install(venv, entry_points)
    assert "Can't deliver some-source -> .pk/mgit-1.0.0/bin/some-source: source does not exist" in logged.pop()

    runez.touch(".pk/mgit-1.0.0/bin/some-source")
    with pytest.raises(SystemExit):
        d.install(venv, entry_points)
    assert "Failed to deliver" in logged.pop()


class SimulatedInstallation:
    def __init__(self, cfg, name, version):
        self.cfg = cfg
        self.name = name
        self.version = version
        self.entry_points = {name: name}
        self.pspec = PackageSpec(cfg, f"{name}=={version}")
        self.pspec.save_manifest(self.entry_points)
        folder = self.cfg.meta.full_path(f"{name}-{version}")
        self.venv = PythonVenv(folder, self.pspec)
        venv_exe = os.path.join(folder, "bin", name)
        runez.write(venv_exe, f"#!/bin/bash\n\necho {version}\n")
        runez.symlink(folder, self.pspec.venv_path(version))
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
