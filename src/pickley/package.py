import os
import sys
from typing import Sequence

import runez
from runez.pyenv import PypiStd, Version

from pickley import abort, LOG, PICKLEY
from pickley.bstrap import uv_env
from pickley.delivery import DeliveryMethod


class PythonVenv:
    def __init__(self, folder, pspec, use_pip=None):
        """
        Args:
            folder (str | pathlib.Path): Target folder
            pspec (pickley.PackageSpec): Package spec to install
            use_pip (bool): Use `pip` instead of `uv`
        """
        self.folder = runez.to_path(folder)
        self.pspec = pspec
        self.python = pspec.python or pspec.cfg.find_python(pspec)
        if use_pip is None:
            pm = pspec.cfg.package_manager(pspec)
            use_pip = sys.version_info[:2] <= (3, 7) if pm is None else pm == "pip"

        self.use_pip = use_pip

    def __repr__(self):
        return runez.short(self.folder)

    def create_venv(self):
        if self.use_pip:
            return self.create_venv_with_pip()

        return self.create_venv_with_uv()

    def create_venv_with_uv(self):
        uv_path = self.pspec.cfg.find_uv()
        r = runez.run(uv_path, "-q", "venv", "-p", self.python.executable, self.folder)
        venv_python = self.folder / "bin/python"
        if venv_python.is_symlink():
            # `uv` fully expands symlinks, use the simplest location instead
            # This would replace for example `.../python-3.10.1/bin/python3.10` with for example `/usr/local/bin/python-3.10`
            actual_path = venv_python.resolve()
            installation = self.pspec.cfg.available_pythons.find_python(actual_path)
            if installation.executable != actual_path:
                runez.symlink(installation.executable, venv_python, overwrite=True)

        # Provide a convenience `pip` wrapper, this will allow to conveniently inspect an installed venv with for example:
        # .../.pk/package-M.m.p/bin/pip freeze
        pip_path = self.folder / "bin/pip"
        pip_wrapper = '#!/bin/sh -e\n\nVIRTUAL_ENV="$(cd $(dirname $0)/..; pwd)" exec uv pip "$@"'
        runez.write(pip_path, pip_wrapper)
        runez.make_executable(pip_path)
        return r

    def create_venv_with_pip(self):
        runez.ensure_folder(self.folder, clean=True, logger=False)
        runez.run(self.python.executable, "-mvenv", self.folder)
        if self.python.mm <= "3.7":
            # Older versions of python come with very old `ensurepip`, sometimes pip 9.0.1 from 2016
            # pip versions newer than 21.3.1 for those old pythons is also known not to work
            self.run_pip("install", "-U", "pip==21.3.1")

    def pip_install(self, *args):
        """`pip install` into target venv`"""
        if self.use_pip:
            return self.run_pip("install", "-i", self.pspec.index, *args)

        quiet = () if runez.log.debug else ("-q",)
        return self.run_uv(*quiet, "pip", "install", *args, passthrough=runez.log.debug)

    def run_uv(self, *args, **kwargs):
        uv_path = self.pspec.cfg.find_uv()
        assert self.pspec.dashed != "uv"
        env = uv_env(mirror=self.pspec.index, venv=self.folder, logger=LOG.debug)
        return runez.run(uv_path, *args, env=env, **kwargs)

    def run_pip(self, *args, **kwargs):
        kwargs.setdefault("fatal", False)
        r = self.run_python("-mpip", *args, **kwargs)
        if r.failed:
            message = "\n".join(simplified_pip_error(r.error, r.output))
            abort(message)

        return r

    def run_python(self, *args, **kwargs):
        """Run python from this venv with given args"""
        return runez.run(self.folder / "bin/python", *args, **kwargs)

    def actual_package_name(self, package_name):
        if package_name == "ansible":
            # Is there a better way to detect weird indirections like ansible does?
            if self.pspec.desired_track and self.pspec.desired_track.version:
                version = Version(self.pspec.desired_track.version)
                if version < 4:
                    return "ansible-base"

            return "ansible-core"

        return package_name

    def entry_points(self) -> Sequence[str]:
        """Entry-points for `self.pspec` package in its virtual environment"""
        package_name = self.pspec.dashed
        if package_name == "uv":
            return "uv", "uvx"

        if package_name == PICKLEY or (runez.DRYRUN and not runez.is_executable(self.folder / "bin/python")):
            return (package_name,)

        # Use `uv pip show` to get location on disk and version of package
        package_name = self.actual_package_name(package_name)
        if self.use_pip:
            r = self.run_pip("show", package_name, dryrun=False)

        else:
            r = self.run_uv("pip", "show", package_name, dryrun=False, fatal=False)

        runez.abort_if(r.failed or not r.output, f"`pip show` failed for '{package_name}': {r.full_output}")
        location = None
        version = None
        for line in r.output.splitlines():
            if line.startswith("Location:"):
                location = line.partition(":")[2].strip()

            if line.startswith("Version:"):
                version = line.partition(":")[2].strip()

            if location and version:
                break

        runez.abort_if(not location or not version, f"Failed to parse `pip show` output for '{package_name}': {r.full_output}")
        wheel_name = PypiStd.std_wheel_basename(package_name)
        folder = os.path.join(location, f"{wheel_name}-{version}.dist-info")
        declared_entry_points = runez.file.ini_to_dict(os.path.join(folder, "entry_points.txt"))
        if declared_entry_points and "console_scripts" in declared_entry_points:
            console_scripts = declared_entry_points["console_scripts"]
            if console_scripts and isinstance(console_scripts, dict):
                return sorted(console_scripts)  # Package has a standard entry_points.txt file

        # No standard entry_points.txt, let's try to find executables in bin/
        # For example: `awscli` does this (no proper entry points, has bin-scripts only)
        records = os.path.join(folder, "RECORD")
        entry_points = []
        for line in runez.readlines(records):
            if line.startswith(".."):
                path = line.partition(",")[0]
                dirname = os.path.dirname(path)
                if os.path.basename(dirname) == "bin":
                    entry_points.append(os.path.basename(path))

        if not entry_points and "tox" in declared_entry_points:
            # Special case for `tox` plugins (is there a better way to detect this?)
            entry_points.append("tox")

        return entry_points


def simplified_pip_error(error, output):
    lines = error or output or "pip failed without output"
    lines = lines.splitlines()
    for line in lines:
        if line and "You are using pip" not in line and "You should consider upgrading" not in line:
            yield line


class Packager:
    """Ancestor to package/install implementations"""

    @staticmethod
    def install(pspec, ping=True, no_binary=None):
        """
        Args:
            pspec (pickley.PackageSpec): Targeted package spec
            ping (bool): If True, touch .ping file when done
        """

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        """Package current folder

        Args:
            pspec (pickley.PackageSpec): Targeted package spec
            build_folder (str): Folder to use as build cache
            dist_folder (str): Folder where to produce package
            requirements (pickley.cli.Requirements): Additional requirements (same convention as pip, can be package names or package specs)
            run_compile_all (bool): Call 'compileall' on generated package?

        Returns:
            (list | None): List of packaged executables
        """
        raise NotImplementedError("Packaging with packager '{packager}' is not supported")


class VenvPackager(Packager):
    """Install in a virtualenv"""

    @staticmethod
    def install(pspec, ping=True, no_binary=None):
        delivery = DeliveryMethod.delivery_method_by_name(pspec.settings.delivery)
        delivery.ping = ping
        venv = PythonVenv(pspec.venv_path(pspec.desired_track.version), pspec)
        if pspec.dashed == "uv":
            # Special case for uv: it does not need a venv
            from pickley.bstrap import download_uv

            download_uv(pspec.cfg.cache.path, venv.folder, version=pspec.desired_track.version, dryrun=runez.DRYRUN)

        else:
            args = []
            if no_binary:
                args.append("--no-binary")
                args.append(no_binary)

            venv.create_venv()
            args.extend(pspec.pip_spec())
            venv.pip_install(*args)

        entry_points = venv.entry_points()
        if not entry_points:
            pspec.delete_all_files()
            abort(f"Can't install '{runez.bold(pspec.dashed)}', it is {runez.red('not a CLI')}")

        actual_entry_points = [n for n in entry_points if "_completer" not in n]
        runez.abort_if(not actual_entry_points, f"No entry points found for '{pspec.dashed}'")
        return delivery.install(venv, actual_entry_points)

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        runez.ensure_folder(dist_folder, clean=True, logger=False)
        venv = PythonVenv(dist_folder, pspec, use_pip=True)
        venv.create_venv()
        for requirement_file in requirements.requirement_files:
            venv.pip_install("-r", requirement_file)

        if requirements.additional_packages:
            venv.pip_install(*requirements.additional_packages)

        venv.pip_install(requirements.project)
        if run_compile_all:
            venv.run_python("-mcompileall", dist_folder)

        entry_points = venv.entry_points()
        if entry_points:
            result = []
            for name in entry_points:
                result.append(str(venv.folder / f"bin/{name}"))

            return result
