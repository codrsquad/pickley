import os
from pathlib import Path
from typing import Optional, Sequence, TYPE_CHECKING

import runez
from runez.pyenv import PythonInstallation, Version

from pickley import bstrap, CFG, PackageSpec, TrackedManifest
from pickley.delivery import DeliveryMethod, DeliveryMethodSymlink, DeliveryMethodWrap

if TYPE_CHECKING:
    from pickley.cli import Requirements


class PythonVenv:
    """Python virtual environment as seen by pickley, typically in <base>/.pk/<package>-<version>/"""

    _uv_path = None  # Overridden in tests

    def __init__(self, folder: Path, python: PythonInstallation, package_manager: str):
        """
        Parameters
        ----------
        folder : Path
            Folder where to create the venv
        python : PythonInstallation
            Python installation to use
        package_manager : str
            Package manager to use ("pip" or "uv")
        """
        self.folder = folder
        self.python = python
        self.package_manager = package_manager
        self.use_pip = package_manager == "pip"
        self.groom_uv_venv = True
        self.uv_seed = None  # Long term: CLIs should not assume setuptools is always there... (same problem with py3.12)
        self.logger = runez.UNSET

    def __repr__(self):
        return runez.short(self.folder)

    @classmethod
    def find_uv(cls):
        if cls._uv_path is None:
            path = CFG.base / "uv"
            if runez.is_executable(path):
                cls._uv_path = path

            else:
                return bstrap.find_uv(CFG.base)

        return cls._uv_path

    def create_venv(self):
        runez.abort_if(self.python.problem, f"Invalid python: {self.python}")
        if self.use_pip:
            return self.create_venv_with_pip()

        return self.create_venv_with_uv()

    def create_venv_with_uv(self):
        uv_path = PythonVenv.find_uv()
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
        runez.ensure_folder(self.folder, clean=True, logger=self.logger)
        runez.run(self.python.executable, "-mvenv", self.folder, logger=self.logger)
        self._run_pip("install", "-U", *bstrap.pip_auto_upgrade())

    def pip_install(self, *args, fatal=True, no_deps=False, quiet=None):
        """`pip install` into target venv`"""
        if quiet is None:
            quiet = not runez.log.debug

        cmd = []
        if quiet:
            cmd.append("-q")

        if not self.use_pip:
            cmd.append("pip")

        cmd.append("install")
        if no_deps:
            cmd.append("--no-deps")

        cmd.extend(args)
        if self.use_pip:
            return self._run_pip(*cmd, fatal=fatal)

        return self.run_uv(*cmd, passthrough=not quiet, fatal=fatal)

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
        uv_path = PythonVenv.find_uv()
        env = dict(os.environ)
        env["VIRTUAL_ENV"] = self.folder
        kwargs.setdefault("logger", self.logger)
        return runez.run(uv_path, *args, env=env, **kwargs)

    def run_python(self, *args, **kwargs):
        """Run python from this venv with given args"""
        kwargs.setdefault("logger", self.logger)
        return runez.run(self.folder / "bin/python", *args, **kwargs)

    def _run_pip(self, *args, **kwargs):
        kwargs.setdefault("fatal", False)
        r = self.run_python("-mpip", *args, **kwargs)
        if r.failed:
            ignored = ("You are using pip", "You should consider upgrading", "Ignored the following yanked")
            r.error = runez.joined(line for line in r.error.splitlines() if not any(x in line for x in ignored))
            r.output = runez.joined(line for line in r.output.splitlines() if not any(x in line for x in ignored))

        return r


def find_symbolic_invoker() -> str:
    """Symbolic major/minor symlink to invoker, when applicable"""
    invoker = runez.SYS_INFO.invoker_python
    folder = invoker.real_exe.parent.parent
    v = Version.extracted_from_text(folder.name)
    if v and v.given_components_count == 3:
        # For setups that provide a <folder>/pythonM.m -> <folder>/pythonM.m.p symlink, prefer the major/minor variant
        candidates = [folder.parent / folder.name.replace(v.text, v.mm), folder.parent / f"python{v.mm}"]
        for path in candidates:
            if path.exists():
                return path

    return invoker.executable  # pragma: no cover


class VenvPackager:
    """Install in a virtualenv"""

    @staticmethod
    def delivery_method_for(pspec: PackageSpec) -> DeliveryMethod:
        return VenvPackager.delivery_method_by_name(pspec.delivery_method_name)

    @staticmethod
    def delivery_method_by_name(name: str) -> DeliveryMethod:
        if name == "wrap":
            return DeliveryMethodWrap()

        if name == "symlink":
            return DeliveryMethodSymlink()

        return runez.abort(f"Unknown delivery method '{runez.red(name)}'")

    @staticmethod
    def install(pspec: PackageSpec) -> TrackedManifest:
        """
        Parameters
        ----------
        pspec : PackageSpec
            Targeted package spec

        Returns
        -------
        TrackedManifest
            Installed package manifest
        """
        venv_settings = pspec.settings.venv_settings()
        venv = PythonVenv(pspec.target_installation_folder, venv_settings.python_installation, venv_settings.package_manager)
        if pspec.canonical_name == "uv":
            # Special case for uv: it does not need a venv
            bstrap.download_uv(venv.folder, version=pspec.target_version, dryrun=runez.DRYRUN)

        else:
            venv.create_venv()
            venv.pip_install(pspec.resolved_info.pip_spec)

        delivery = VenvPackager.delivery_method_for(pspec)
        return delivery.install(pspec)

    @staticmethod
    def package(pspec: PackageSpec, dist_folder: Path, requirements: "Requirements", run_compile_all: bool) -> Optional[Sequence[Path]]:
        """
        Package `pspec` and `requirements` into a virtual env in `dist_folder`.

        Parameters
        ----------
        pspec : PackageSpec
            Targeted package spec
        dist_folder : Path
            Folder where to produce package
        requirements : Requirements
            Additional requirements (same convention as pip, can be package names or package specs)
        run_compile_all : bool
            Run `-mcompileall` on generated package?

        Returns
        -------
        Optional[Sequence[Path]]
            List of packaged executables
        """
        runez.ensure_folder(dist_folder, clean=True)
        python = CFG.available_pythons.find_python(pspec.settings.python or find_symbolic_invoker())
        venv = PythonVenv(dist_folder, python, package_manager="pip")
        venv.create_venv()
        for requirement_file in requirements.requirement_files:
            venv.pip_install("-r", requirement_file)

        if requirements.additional_packages:
            venv.pip_install(*requirements.additional_packages)

        venv.pip_install(requirements.project)
        if run_compile_all:
            venv.run_python("-mcompileall", dist_folder)

        entrypoints = pspec.resolved_info.entrypoints
        if entrypoints:
            result = []
            for name in entrypoints:
                result.append(venv.folder / "bin/" / name)

            return result
