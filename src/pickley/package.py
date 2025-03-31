import logging
import os
from pathlib import Path
from typing import List, TYPE_CHECKING

import runez
from runez.pyenv import Version

from pickley import bstrap, CFG, PackageSpec, TrackedManifest, VenvSettings
from pickley.delivery import DeliveryMethod, DeliveryMethodSymlink, DeliveryMethodWrap

if TYPE_CHECKING:
    from pickley.cli import Requirements

LOG = logging.getLogger(__name__)


class PythonVenv:
    """Python virtual environment as seen by pickley, typically in <base>/.pk/<package>-<version>/"""

    def __init__(self, folder: Path, settings: VenvSettings, groom_uv_venv=True):
        """
        Parameters
        ----------
        folder : Path
            Folder where to create the venv
        settings : VenvSettings
            Settings to use for this venv
        groom_uv_venv : bool
            If true, ensure the python symlink is as "canonical" as possible
        """
        self.folder = folder
        self.settings = settings
        self.groom_uv_venv = groom_uv_venv
        self.logger = runez.UNSET
        self.use_pip = settings.package_manager == "pip"

    def __repr__(self):
        return runez.short(self.folder)

    def create_venv(self):
        python = self.settings.python_installation
        runez.abort_if(not python or python.problem, f"Invalid python: {python}")
        runez.ensure_folder(self.folder, clean=True, logger=self.logger)
        if self.use_pip:
            return self.create_venv_with_pip()

        return self.create_venv_with_uv()

    def create_venv_with_uv(self):
        uv_path = CFG.uv_bootstrap.uv_path
        seed = "--seed" if self.settings.uv_seed else None
        env = dict(os.environ)
        env.pop("UV_VENV_SEED", None)  # We explicitly state `--seed` if/when needed
        r = runez.run(uv_path, "-q", "venv", seed, "-p", self.settings.python_executable, self.folder, logger=self.logger, env=env)
        if self.groom_uv_venv:
            venv_python = self.folder / "bin/python"
            if venv_python.is_symlink():
                # `uv` fully expands symlinks, use the simplest location instead
                # This would replace `.../python-3.10.1/bin/python3.10` with for example `/usr/local/bin/python3.10`
                actual_path = venv_python.resolve()
                if self.settings.python_executable != actual_path:
                    runez.symlink(self.settings.python_executable, venv_python, overwrite=True, logger=self.logger)

            # Provide a convenience `pip` wrapper, this will allow to conveniently inspect an installed venv with for example:
            # .../.pk/package-M.m.p/bin/pip freeze
            pip_path = self.folder / "bin/pip"
            pip_wrapper = '#!/bin/sh -e\n\nVIRTUAL_ENV="$(cd $(dirname $0)/..; pwd)" exec uv pip "$@"'
            runez.write(pip_path, pip_wrapper, logger=None)
            runez.make_executable(pip_path, logger=None)
            runez.log.trace(f"Created pip wrapper {pip_path}")

        return r

    def create_venv_with_pip(self):
        runez.run(self.settings.python_executable, "-mvenv", self.folder, logger=self.logger)
        return self._run_py_pip("install", "-U", *bstrap.pip_auto_upgrade())

    def pip_install(self, *args, fatal=True, no_deps=False, quiet=None, env=None):
        """`pip install` into target venv`"""
        cmd = list(self._auto_quiet_args("install", no_deps and "--no-deps", quiet=quiet))
        if quiet is True:
            passthrough = False

        else:
            passthrough = quiet is False or "-q" not in cmd

        if self.use_pip:
            return self._run_py_pip(*cmd, *args, fatal=fatal, passthrough=passthrough, env=env)

        return self._run_uv(*cmd, *args, fatal=fatal, passthrough=passthrough, env=env)

    def run_pip(self, command, *args, **kwargs):
        """Run `pip` command, this only works for commands that are common between `pip` and `uv`"""
        if self.use_pip:
            return self._run_py_pip(command, *args, **kwargs)

        return self._run_uv("pip", command, *args, **kwargs)

    def run_python(self, *args, **kwargs):
        """Run python from this venv with given args"""
        kwargs.setdefault("logger", self.logger)
        return runez.run(self.folder / "bin/python", *args, **kwargs)

    def _run_py_pip(self, *args, **kwargs):
        r = self.run_python("-mpip", *args, **kwargs)
        if r.failed:
            ignored = ("You are using pip", "You should consider upgrading", "Ignored the following yanked")
            if r.error:
                r.error = runez.joined(line for line in r.error.splitlines() if not any(x in line for x in ignored))

            if r.output:
                r.output = runez.joined(line for line in r.output.splitlines() if not any(x in line for x in ignored))

        return r

    def _run_uv(self, *args, **kwargs):
        kwargs.setdefault("logger", self.logger)
        uv_path = CFG.uv_bootstrap.uv_path
        env = dict(kwargs.get("env") or os.environ)
        env["VIRTUAL_ENV"] = str(self.folder)
        kwargs["env"] = env
        return runez.run(uv_path, *args, **kwargs)

    def _auto_quiet_args(self, command, *additional, quiet=None):
        """Automatically add -q if not in verbose mode"""
        if quiet is None:
            quiet = CFG.verbosity < 2 or not runez.color.is_coloring()

        if quiet:
            yield "-q"

        if not self.use_pip:
            yield "pip"

        yield command
        for arg in additional:
            if arg:
                yield arg


def find_symbolic_invoker() -> str:
    """Symbolic major/minor symlink to invoker, when applicable"""
    invoker = runez.SYS_INFO.invoker_python
    folder = invoker.real_exe.parent.parent
    LOG.info("Invoker python: %s", invoker)
    v = Version.extracted_from_text(folder.name)
    found = invoker.executable
    if v and v.given_components_count == 3:
        # For setups that provide a <folder>/pythonM.m -> <folder>/pythonM.m.p symlink, prefer the major/minor variant
        candidates = []
        for candidate in (folder.name.replace(v.text, v.mm), f"python{v.mm}"):
            candidates.append(folder.parent / candidate)
            candidates.append(folder.parent / candidate / "bin" / f"python{v.mm}")

        for path in candidates:
            if runez.is_executable(path):
                LOG.info("Found symbolic invoker: %s", path)
                found = path
                break

    return found and str(found)


class VenvPackager:
    """Install in a virtualenv"""

    @staticmethod
    def delivery_method_for(pspec: PackageSpec) -> DeliveryMethod:
        return VenvPackager.delivery_method_by_name(pspec.delivery_method_name())

    @staticmethod
    def delivery_method_by_name(name: str) -> DeliveryMethod:
        if name == "wrap":
            return DeliveryMethodWrap()

        if name == "symlink":
            return DeliveryMethodSymlink()

        return runez.abort(f"Unknown delivery method '{runez.red(name)}'")

    @staticmethod
    def install(pspec: PackageSpec, fatal=True) -> TrackedManifest:
        """
        Parameters
        ----------
        pspec : PackageSpec
            Targeted package spec
        fatal : bool
            If true, abort on failure

        Returns
        -------
        TrackedManifest
            Installed package manifest
        """
        if pspec.is_uv:
            # Special case for uv: it does not need a venv and lives at the root of the base, without a wrapper
            if pspec.currently_installed_version != pspec.target_version:
                uv_tmp = CFG.uv_bootstrap.download_uv(version=pspec.target_version, dryrun=runez.DRYRUN)
                runez.move(uv_tmp / "uv", CFG.base / "uv")
                runez.move(uv_tmp / "uvx", CFG.base / "uvx")
                runez.delete(uv_tmp)

            manifest = pspec.save_manifest()
            return manifest

        venv_settings = pspec.settings.venv_settings()
        venv = PythonVenv(pspec.target_installation_folder(), venv_settings)
        venv.create_venv()
        r = venv.pip_install(pspec.resolved_info.pip_spec, fatal=fatal)
        if r.succeeded:
            delivery = VenvPackager.delivery_method_for(pspec)
            return delivery.install(pspec)

    @staticmethod
    def package(pspec: PackageSpec, dist_folder: Path, requirements: "Requirements", run_compile_all: bool) -> List[Path]:
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
        List[Path]
            List of packaged executables
        """
        if not pspec.settings.python:
            pspec.settings.python = find_symbolic_invoker()

        venv_settings = pspec.settings.venv_settings()
        venv = PythonVenv(dist_folder, venv_settings)
        venv.logger = print
        print(f"Packaging '{pspec}' into '{runez.short(dist_folder)}' with {venv_settings.package_manager} and {venv_settings.python_spec}")
        venv.create_venv()
        for requirement_file in requirements.requirement_files:
            venv.pip_install("-r", requirement_file, quiet=False)

        if requirements.additional_packages:
            venv.pip_install(*requirements.additional_packages, quiet=False)

        venv.pip_install(requirements.project, quiet=False)
        if run_compile_all:
            r = venv.run_python("-mcompileall", dist_folder, fatal=False)
            if r.failed:
                print("-mcompileall failed:")
                output = simplified_compileall(r.full_output)
                print(output)
                runez.abort(f"Failed to run `python -mcompileall` on {runez.red(dist_folder)}")

        return [venv.folder / "bin" / name for name in pspec.resolved_info.entrypoints]


def simplified_compileall(text):
    return runez.joined(_compileall_filter(text), delimiter="\n")


def _compileall_filter(text):
    prev = None
    skipped = 0
    for line in text.splitlines():
        if line.startswith(("Listing ", "Compiling ")):
            prev = line
            skipped += 1

        else:
            if prev:
                if skipped > 1:
                    yield "..."

                yield prev  # Show last "Listing" or "Compiling" line for context (what follows is reported errors)
                prev = None
                skipped = 0

            yield line
