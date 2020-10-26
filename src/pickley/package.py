import logging
import os
import platform
import re

import runez

from pickley import abort, PICKLEY, VIRTUALENV
from pickley.delivery import DeliveryMethod
from pickley.env import valid_exe


LOG = logging.getLogger(__name__)
RE_BIN_SCRIPT = re.compile(r"^[./]+/bin/([-a-z0-9_.]+)$", re.IGNORECASE)
PLATFORM = platform.system().lower()


def entry_points_from_txt(path):
    metadata = runez.file.ini_to_dict(path, default={})
    return metadata.get("console_scripts")


def entry_points_from_metadata(path):
    metadata = runez.read_json(path, default={})
    return metadata.get("extensions", {}).get("python.commands", {}).get("wrap_console")


def bundled_virtualenv(cfg, folder, python):
    bin = os.path.dirname(cfg.pickley_program_path)
    virtualenv = os.path.join(bin, VIRTUALENV)
    if not valid_exe(virtualenv):
        raise Exception()

    runez.run(virtualenv, "-p", python.executable, folder)


def virtualenv_zipapp(python):
    if python.major < 3:
        return "https://bootstrap.pypa.io/virtualenv/%s.%s/virtualenv.pyz" % (python.major, python.minor)

    return "https://bootstrap.pypa.io/virtualenv/virtualenv.pyz"


def download_command(virtualenv, url):
    wget = runez.which("wget")
    if wget:
        return [wget, "-O%s" % virtualenv, url]

    return ["curl", "-s", "-o", virtualenv, url]


def bootstrapped_virtualenv(cfg, folder, python):
    virtualenv = os.path.realpath(os.path.join(folder, "virtualenv.pyz"))

    url = virtualenv_zipapp(python)
    args = download_command(virtualenv, url)
    runez.run(*args)
    runez.run(python.executable, virtualenv, folder)
    runez.delete(virtualenv)


VIRTUALENV_CHAIN = runez.FallbackChain(bundled_virtualenv, bootstrapped_virtualenv)


class PythonVenv(object):

    def __init__(self, pspec, folder=None, python=None, index=None, force_virtualenv=None):
        """
        Args:
            pspec (pickley.PackageSpec): Package spec to install
            folder (str | None): Target folder (default: pspec.install_path)
            python (pickley.env.PythonInstallation): Python to use (default: pspec.python)
            index (str | None): Optional custom pypi index to use (default: pspec.index)
            force_virtualenv (bool): If True, use virtualenv instead of built-in `venv` module
        """
        self.folder = folder if folder is not None else pspec.install_path
        if python is None:
            python = pspec.python

        self.index = index or pspec.index
        if self.folder:
            if force_virtualenv is None:
                # https://github.com/tox-dev/tox/issues/1689
                force_virtualenv = PLATFORM == "darwin" and pspec.dashed == "tox"

            if python.problem:
                abort("Can't create virtualenv with python '%s': %s" % (runez.bold(python), runez.red(python.problem)))

            runez.ensure_folder(self.folder, clean=True, logger=False)
            if force_virtualenv or python.needs_virtualenv:
                VIRTUALENV_CHAIN(pspec.cfg, self.folder, python)

            else:
                python.run("-mvenv", self.folder)

            if pspec.dashed == "tox":
                self.pip_install("-U", "pip", "setuptools", "wheel")

    def __repr__(self):
        return runez.short(self.folder)

    def bin_path(self, name):
        """
        Args:
            name (str): File name

        Returns:
            (str): Full path to this <venv>/bin/<name>
        """
        return os.path.join(self.folder, "bin", name)

    def _is_venv_exe(self, path):
        """
        Args:
            path (str): Path to file to examine

        Returns:
            (bool): True if 'path' points to a python executable part of this venv
        """
        if runez.is_executable(path):
            lines = runez.readlines(path, default=None, first=2, errors="ignore")
            if lines and len(lines) > 1 and lines[0].startswith("#!"):
                if self.folder in lines[0] or self.folder in lines[1]:  # 2 variants: "#!<folder>/bin/python" or 'exec "<folder>/bin/..."'
                    return True

    def find_entry_points(self, pspec):
        """
        Args:
            pspec (pickley.PackageSpec): Package spec to look for

        Returns:
            (dict | None): Entry points, when available
        """
        if runez.DRYRUN:
            return {pspec.dashed: "dryrun"}  # Pretend an entry point exists in dryrun mode

        if pspec.dashed == PICKLEY:
            return {PICKLEY: pspec.cfg.pickley_program_path}

        r = self.run_python("-mpip", "show", "-f", pspec.dashed, fatal=False)
        if r.succeeded:
            location = None
            in_files = False
            bin_scripts = None
            for line in r.output.splitlines():
                if in_files:
                    if not location:
                        break

                    line = line.strip()
                    m = RE_BIN_SCRIPT.match(line)
                    if m:
                        name = m.group(1)
                        if "_completer" not in name:
                            path = os.path.abspath(os.path.join(location, line))
                            if self._is_venv_exe(path):
                                if bin_scripts is None:
                                    bin_scripts = {}

                                bin_scripts[name] = path

                    elif line.endswith("entry_points.txt"):
                        ep = entry_points_from_txt(os.path.join(location, line))
                        if ep:
                            return ep

                    elif line.endswith("metadata.json"):
                        ep = entry_points_from_metadata(os.path.join(location, line))
                        if ep:
                            return ep

                elif line.startswith("Location:"):
                    location = line.partition(":")[2].strip()

                elif line.startswith("Files:"):
                    in_files = True

            return bin_scripts

    def pip_install(self, *args):
        """Allows to not forget to state the -i index..."""
        r = self._run_pip("install", "-i", self.index, *args, fatal=False)
        if r.failed:
            message = "\n".join(simplified_pip_error(r.error, r.output))
            abort(message)

        return r

    def pip_wheel(self, *args):
        """Allows to not forget to state the -i index..."""
        return self._run_pip("wheel", "-i", self.index, *args)

    def run_python(self, *args, **kwargs):
        """Run python from this venv with given args"""
        return runez.run(self.bin_path("python"), *args, **kwargs)

    def _run_pip(self, *args, **kwargs):
        return self.run_python("-mpip", "-v", *args, **kwargs)


def simplified_pip_error(error, output):
    lines = error or output or "pip failed without output"
    lines = lines.splitlines()
    for line in lines:
        if line and "You are using pip" not in line and "You should consider upgrading" not in line:
            yield line


class Packager(object):
    """Ancestor to package/install implementations"""

    @staticmethod
    def install(pspec, ping=True):
        """
        Args:
            pspec (pickley.PackageSpec): Targeted package spec
        """
        raise NotImplementedError("Installation with packager '{packager}' is not supported")

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements):
        """Package current folder

        Args:
            pspec (pickley.PackageSpec): Targeted package spec
            build_folder (str): Folder to use as build cache
            dist_folder (str): Folder where to produce package
            requirements (list): Additional requirements (same convention as pip, can be package names or package specs)

        Returns:
            (list | None): List of packaged executables
        """
        raise NotImplementedError("Packaging with packager '{packager}' is not supported")


class PexPackager(Packager):
    """Package via pex (https://pypi.org/project/pex/)"""

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements):
        runez.delete("~/.pex", fatal=False, logger=False)
        wheels = os.path.join(build_folder, "wheels")
        runez.ensure_folder(build_folder, clean=True)
        pex_venv = PythonVenv(pspec, folder=os.path.join(build_folder, "pex-venv"))
        pex_venv.pip_install("wheel", "pex==1.6.7", *requirements)
        pex_venv.pip_wheel("-v", "--cache-dir", wheels, "--wheel-dir", wheels, *requirements)
        entry_points = pex_venv.find_entry_points(pspec)
        if entry_points:
            result = []
            for name in entry_points:
                target = os.path.join(dist_folder, name)
                runez.delete(target, logger=False)
                pex_venv.run_python(
                    "-mpex", "-v", "--no-compile", "--no-pypi", "--pre", "--cache-dir", wheels, "-f", wheels,
                    "-c%s" % name, "-o%s" % target, name,
                    "--python-shebang", "/usr/bin/env python%s" % pspec.python.major,
                )
                result.append(target)

            return result


class VenvPackager(Packager):
    """Install in a virtualenv"""

    @staticmethod
    def install(pspec, ping=True):
        delivery = DeliveryMethod.delivery_method_by_name(pspec.settings.delivery)
        delivery.ping = ping
        args = [pspec.specced]
        extras = None
        if pspec.dashed == PICKLEY:
            # Inject extra packages for pickley, to help bootstrap
            extras = ["virtualenv", "requests"]
            project_path = runez.log.project_path()
            if project_path:
                args = ["-e", project_path]  # Development mode (running from source checkout)

        venv = PythonVenv(pspec)
        venv.pip_install(*args)
        if extras:
            venv.run_python("-mpip", "install", *extras, fatal=False)

        entry_points = venv.find_entry_points(pspec)
        if not entry_points:
            runez.delete(pspec.meta_path)
            abort("Can't install '%s', it is %s" % (runez.bold(pspec.dashed), runez.red("not a CLI")))

        return delivery.install(pspec, venv, entry_points)

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements):
        runez.ensure_folder(dist_folder, clean=True)
        venv = PythonVenv(pspec, folder=dist_folder)
        venv.pip_install(*requirements)
        venv.run_python("-mcompileall", dist_folder)
        entry_points = venv.find_entry_points(pspec)
        if entry_points:
            result = []
            for name in entry_points:
                result.append(venv.bin_path(name))

            return result
