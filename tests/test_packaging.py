import os
import time

import runez
from mock import patch

from pickley import system
from pickley.package import DELIVERERS, find_prefix, PACKAGERS, VersionMeta
from pickley.settings import Definition

from .conftest import INEXISTING_FILE, verify_abort


def test_delivery(temp_base):
    # Test copy folder
    deliver = DELIVERERS.get("copy")("tox")
    target = os.path.join(temp_base, "t1")
    source = os.path.join(temp_base, "t1-source")
    source_file = os.path.join(source, "foo")
    runez.touch(source_file)
    deliver.install(target, source)
    assert os.path.isdir(target)
    assert os.path.isfile(os.path.join(target, "foo"))

    # Test copy file
    deliver = DELIVERERS.get("copy")("tox")
    target = os.path.join(temp_base, "t2")
    source = os.path.join(temp_base, "t2-source")
    runez.touch(source)
    deliver.install(target, source)
    assert os.path.isfile(target)

    # Test symlink
    deliver = DELIVERERS.get("symlink")("tox")
    target = os.path.join(temp_base, "l2")
    source = os.path.join(temp_base, "l2-source")
    runez.touch(source)
    deliver.install(target, source)
    assert os.path.islink(target)

    # Test wrapper
    p = PACKAGERS.get(system.VENV_PACKAGER)("tox")
    assert p.create_symlinks(None) == 0
    assert p.create_symlinks("foo", fatal=False) == -1
    p.executables = ["foo"]
    assert p.create_symlinks("foo:bar", fatal=False) == -1
    assert str(p) == "venv tox"
    target = os.path.join(temp_base, "tox")
    source = os.path.join(temp_base, "tox-source")
    runez.touch(source)
    deliver = DELIVERERS.get("wrap")("tox")
    deliver.install(target, source)
    assert runez.is_executable(target)


def test_bogus_delivery():
    deliver = DELIVERERS.get(system.DEFAULT_DELIVERY)("foo")
    assert "does not exist" in verify_abort(deliver.install, None, INEXISTING_FILE)
    assert "Failed symlink" in verify_abort(deliver.install, None, __file__)


def test_version_meta():
    assert find_prefix({}, "") is None

    v = VersionMeta("foo")

    v2 = VersionMeta("foo")
    assert v.equivalent(v2)
    v2.packager = "bar"
    assert not v.equivalent(v2)

    v2 = VersionMeta("foo")
    assert v.equivalent(v2)
    v2.delivery = "bar"
    assert not v.equivalent(v2)

    assert not v.equivalent(None)
    assert str(v) == "foo: no version"
    assert v.representation(verbose=True) == "foo: no version"
    v.invalidate("some problem")
    assert str(v) == "foo: some problem"
    assert v.problem == "some problem"

    v3 = VersionMeta("foo")
    v3.version = "1.0"
    v3.timestamp = time.time()
    assert v3.still_valid
    v3.timestamp = "foo"
    assert not v3.still_valid


@patch("pickley.package.latest_pypi_version", return_value=None)
@patch("pickley.package.DELIVERERS.resolved", return_value=None)
def test_versions(_, __, temp_base):
    p = PACKAGERS.get("pex")("foo")
    p.pip_wheel = lambda *_: None

    assert p.specced_command() == "pex"
    p.implementation_version = "1.4.5"
    assert p.specced_command() == "pex==1.4.5"
    p.refresh_desired()
    assert p.desired.representation(verbose=True) == "foo: can't determine latest version from pypi (channel: latest, source: pypi)"

    system.SETTINGS.cli.contents["channel"] = "stable"
    p.refresh_desired()
    assert p.desired.representation() == "foo: can't determine stable version"

    p.version = None
    assert "can't determine stable version" in verify_abort(p.install)

    # Without a build folder
    assert p.get_entry_points() is None

    # With an empty build fodler
    runez.ensure_folder(p.build_folder, folder=True)
    assert p.get_entry_points() is None

    # With a bogus wheel
    with runez.CaptureOutput() as logged:
        p.version = "0.0.0"
        whl = os.path.join(p.build_folder, "foo-0.0.0-py2.py3-none-any.whl")
        runez.touch(whl)
        assert p.get_entry_points() is None
        assert "Can't read wheel" in logged
        runez.delete(whl)

        p.refresh_desired()
        assert p.desired.channel == "adhoc"
        assert p.desired.source == "cli"
        assert p.desired.version == "0.0.0"
        assert not p.desired.problem
        p.version = None
        p.refresh_desired()
        assert p.desired.problem

    # Ambiguous package() call
    assert "Need either source_folder or version in order to package" in verify_abort(p.package)

    # Package bogus folder without a setup.py
    p.source_folder = temp_base
    assert "No setup.py" in verify_abort(p.package)

    # Package with a bogus setup.py
    setup_py = os.path.join(temp_base, "setup.py")
    runez.touch(setup_py)
    assert "Could not determine version" in verify_abort(p.package)

    # Provide a minimal setup.py
    runez.write_contents(setup_py, "from setuptools import setup\nsetup(name='foo', version='0.0.0')\n")

    # Package project without entry points
    p.get_entry_points = lambda *_: None
    assert "is not a CLI" in verify_abort(p.required_entry_points)


def get_definition(key, package_name=None):
    if key == "channel":
        return Definition("stable", "test", key)
    if key.startswith("channel.stable."):
        return Definition("1.0", "test", key)
    if key == "delivery":
        return Definition("wrap", "test", key)
    if key == "packager":
        return Definition(system.VENV_PACKAGER, "test", key)
    return None


@patch("runez.resolved_path", side_effect=lambda x, **_: x)
@patch("pickley.settings.SettingsFile.get_definition", side_effect=get_definition)
def test_channel(*_):
    p = PACKAGERS.get(system.VENV_PACKAGER)("foo")
    p.refresh_desired()
    assert p.desired.representation(verbose=True) == "foo 1.0 (as venv wrap, channel: stable, source: test:channel.stable.foo)"

    with runez.CaptureOutput(dryrun=True) as logged:
        p.executables = ["foo/bar"]
        assert p.create_symlinks("foo:baz", fatal=False) == 1
        assert "Would symlink /bar <- baz/bar" in logged.pop()
