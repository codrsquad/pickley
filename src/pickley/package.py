import os
from pathlib import Path
from typing import Sequence

import runez
from runez.pyenv import PypiStd, Version

from pickley import abort, CFG
from pickley.bstrap import default_package_manager, PICKLEY, pip_auto_upgrade, uv_env


class PythonVenv:

    def __init__(self, folder: Path, package_manager: str = None, python_spec: str = None):
        """
        Parameters
        ----------
        folder : Path
            Folder where to create the venv
        package_manager : str
            Override package manager to use
        python_spec : str
            Override python to use
        """
        self.folder = runez.to_path(folder)
        self.python = CFG.available_pythons.find_python(python_spec)
        if package_manager is None:
            package_manager = default_package_manager(self.python.mm.major, self.python.mm.minor)

        self.package_manager = package_manager
        self.use_pip = package_manager == "pip"
        self.groom_uv_venv = True
        self.uv_seed = None  # Long term: CLIs should not assume setuptools is always there... (same problem with py3.12)
        self.logger = runez.UNSET

    def __repr__(self):
        return runez.short(self.folder)

    def create_venv(self):
        runez.abort_if(self.python.problem, f"Invalid python: {self.python}")
        if self.use_pip:
            return self.create_venv_with_pip()

        return self.create_venv_with_uv()

    def create_venv_with_uv(self):
        uv_path = CFG.find_uv()
        seed = "--seed" if self.uv_seed else None
        r = runez.run(uv_path, "-q", "venv", seed, "-p", self.python.executable, self.folder, logger=self.logger)
        if self.groom_uv_venv:
            venv_python = self.folder / "bin/python"
            if venv_python.is_symlink():
                # `uv` fully expands symlinks, use the simplest location instead
                # This would replace for example `.../python-3.10.1/bin/python3.10` with for example `/usr/local/bin/python-3.10`
                actual_path = venv_python.resolve()
                if self.python.executable != actual_path:
                    runez.symlink(self.python.executable, venv_python, overwrite=True, logger=self.logger)

            # Provide a convenience `pip` wrapper, this will allow to conveniently inspect an installed venv with for example:
            # .../.pk/package-M.m.p/bin/pip freeze
            pip_path = self.folder / "bin/pip"
            pip_wrapper = '#!/bin/sh -e\n\nVIRTUAL_ENV="$(cd $(dirname $0)/..; pwd)" exec uv pip "$@"'
            runez.write(pip_path, pip_wrapper, logger=None)
            runez.make_executable(pip_path, logger=None)

        return r

    def create_venv_with_pip(self):
        runez.ensure_folder(self.folder, clean=True, logger=False)
        runez.run(self.python.executable, "-mvenv", self.folder, logger=self.logger)
        self._run_pip("install", "-U", *pip_auto_upgrade())

    def pip_install(self, *args, fatal=True, no_deps=False, quiet=None):
        """`pip install` into target venv`"""
        no_deps = "--no-deps" if no_deps else None
        if quiet is None:
            quiet = not runez.log.debug

        quiet = "-q" if quiet else None
        if self.use_pip:
            return self._run_pip(quiet, "install", no_deps, "-i", CFG.index, *args, fatal=fatal)

        return self.run_uv(quiet, "pip", "install", no_deps, *args, passthrough=not quiet, fatal=fatal)

    def pip_freeze(self):
        """Output of `pip freeze`"""
        if self.use_pip:
            return self._run_pip("freeze")

        return self.run_uv("pip", "freeze")

    def pip_show(self, package_name):
        if self.use_pip:
            return self._run_pip("show", package_name)

        return self.run_uv("pip", "show", package_name)

    def run_uv(self, *args, **kwargs):
        uv_path = CFG.find_uv()
        env = uv_env(mirror=CFG.index, venv=self.folder, logger=self.logger)
        kwargs.setdefault("logger", self.logger)
        return runez.run(uv_path, *args, env=env, **kwargs)

    def run_python(self, *args, **kwargs):
        """Run python from this venv with given args"""
        kwargs.setdefault("logger", self.logger)
        return runez.run(self.folder / "bin/python", *args, **kwargs)

    def get_version_location(self, package_name):
        r = self.pip_show(package_name)
        version = None
        location = None
        for line in r.output.splitlines():
            if line.startswith("Version:"):
                version = line.partition(":")[2].strip()

            if line.startswith("Location:"):
                location = line.partition(":")[2].strip()

            if location and version:
                break

        return version, location

    def actual_package_name(self, package_name):
        if package_name == "ansible":
            # Is there a better way to detect weird indirections like ansible does?
            return "ansible-core"

        return package_name

    def entry_points(self, package_name) -> Sequence[str]:
        """Entry-points for `self.pspec` package in its virtual environment"""
        if package_name == "uv":
            return "uv", "uvx"

        if package_name == PICKLEY or (runez.DRYRUN and not runez.is_executable(self.folder / "bin/python")):
            return (package_name,)

        # Use `uv pip show` to get location on disk and version of package
        package_name = self.actual_package_name(package_name)
        version, location = self.get_version_location(package_name)
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

    def _run_pip(self, *args, **kwargs):
        kwargs.setdefault("fatal", False)
        r = self.run_python("-mpip", *args, **kwargs)
        if r.failed:
            message = "\n".join(simplified_pip_error(r.error, r.output))
            abort(message)

        return r


def simplified_pip_error(error, output):
    lines = error or output or "pip failed without output"
    lines = lines.splitlines()
    for line in lines:
        if line and "You are using pip" not in line and "You should consider upgrading" not in line:
            yield line


class Packager:
    """Ancestor to package/install implementations"""

    @staticmethod
    def install(pspec, no_binary=None):
        """
        Args:
            pspec (pickley.PackageSpec): Targeted package spec
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
    def install(pspec, no_binary=None):
        delivery = pspec.delivery_method
        package_manager = pspec.resolved_info.package_manager
        python_spec = pspec.resolved_info.python_spec
        venv = PythonVenv(pspec.target_installation_folder, package_manager=package_manager, python_spec=python_spec)
        if pspec.canonical_name == "uv":
            # Special case for uv: it does not need a venv
            from pickley.bstrap import download_uv

            download_uv(CFG.cache.path, venv.folder, version=pspec.target_version, dryrun=runez.DRYRUN)

        else:
            args = []
            if no_binary:
                args.append("--no-binary")
                args.append(no_binary)

            venv.create_venv()
            args.extend(pspec.resolved_info.pip_spec)
            venv.pip_install(*args)

        entry_points = venv.entry_points(pspec.canonical_name)
        if not entry_points:
            pspec.delete_all_files()
            abort(f"Can't install '{runez.bold(pspec.canonical_name)}', it is {runez.red('not a CLI')}")

        actual_entry_points = [n for n in entry_points if "_completer" not in n]
        runez.abort_if(not actual_entry_points, f"No entry points found for '{pspec.canonical_name}'")
        return delivery.install(pspec, venv.folder, actual_entry_points)

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        runez.ensure_folder(dist_folder, clean=True, logger=False)
        venv = PythonVenv(dist_folder, package_manager="pip", python_spec=pspec.resolved_info.python_spec)
        venv.create_venv()
        for requirement_file in requirements.requirement_files:
            venv.pip_install("-r", requirement_file)

        if requirements.additional_packages:
            venv.pip_install(*requirements.additional_packages)

        venv.pip_install(requirements.project)
        if run_compile_all:
            venv.run_python("-mcompileall", dist_folder)

        entry_points = venv.entry_points(pspec.canonical_name)
        if entry_points:
            result = []
            for name in entry_points:
                result.append(str(venv.folder / f"bin/{name}"))

            return result
