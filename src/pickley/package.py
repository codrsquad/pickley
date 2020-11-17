import logging
import os
import re

import runez

from pickley import abort, PICKLEY
from pickley.delivery import DeliveryMethod
from pickley.env import python_exe_path


LOG = logging.getLogger(__name__)
RE_BIN_SCRIPT = re.compile(r"^[./]+/bin/([-a-z0-9_.]+)$", re.IGNORECASE)


def entry_points_from_txt(path):
    metadata = runez.file.ini_to_dict(path, default={})
    return metadata.get("console_scripts")


def entry_points_from_metadata(path):
    metadata = runez.read_json(path, default={})
    return metadata.get("extensions", {}).get("python.commands", {}).get("wrap_console")


def download_command(target, url):
    curl = runez.which("curl")
    if curl:
        return [curl, "-s", "-o", target, url]

    return ["wget", "-q", "-O%s" % target, url]


class PythonVenv(object):

    def __init__(self, pspec, folder=None, python=None, index=None):
        """
        Args:
            pspec (pickley.PackageSpec): Package spec to install
            folder (str | None): Target folder (default: pspec.install_path)
            python (pickley.env.PythonInstallation): Python to use (default: pspec.python)
            index (str | None): Optional custom pypi index to use (default: pspec.index)
        """
        if folder is None:
            folder = pspec.install_path

        if python is None:
            python = pspec.python

        self.folder = folder
        self.index = index or pspec.index
        if folder:
            runez.ensure_folder(folder, clean=True, logger=False)
            if pspec.cfg.bundled_virtualenv_path:
                runez.run(pspec.cfg.bundled_virtualenv_path, "-p", python.executable, folder)
                return

            if python.major > 2 and pspec.dashed != "tox":
                # See https://github.com/tox-dev/tox/issues/1689
                runez.run(python.executable, "-mvenv", self.folder)
                self.pip_install("-U", "pip", "setuptools", "wheel")
                return

            runez.ensure_folder(pspec.cfg.cache.path, logger=False)
            zipapp = os.path.realpath(pspec.cfg.cache.full_path("virtualenv.pyz"))
            args = download_command(zipapp, "https://bootstrap.pypa.io/virtualenv/virtualenv.pyz")
            runez.run(*args)
            runez.run(python.executable, zipapp, folder)
            runez.delete(zipapp, fatal=False, logger=False)

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
        return runez.run(python_exe_path(self.folder), *args, **kwargs)

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
    def package(pspec, build_folder, dist_folder, requirements, compile):
        """Package current folder

        Args:
            pspec (pickley.PackageSpec): Targeted package spec
            build_folder (str): Folder to use as build cache
            dist_folder (str): Folder where to produce package
            requirements (list): Additional requirements (same convention as pip, can be package names or package specs)
            compile (bool): Call 'compileall' on generated package?

        Returns:
            (list | None): List of packaged executables
        """
        raise NotImplementedError("Packaging with packager '{packager}' is not supported")


class PexPackager(Packager):
    """Package via pex (https://pypi.org/project/pex/)"""

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, compile):
        runez.ensure_folder(build_folder, clean=True)
        if pspec.python.major < 3:
            abort("Packaging with pex is not supported any more with python2")

        pex_root = os.path.join(build_folder, "pex-root")
        tmp = os.path.join(build_folder, "pex-tmp")
        wheels = os.path.join(build_folder, "wheels")
        runez.ensure_folder(tmp, logger=False)
        runez.ensure_folder(wheels, logger=False)
        pex_venv = PythonVenv(pspec, folder=os.path.join(build_folder, "pex-venv"))
        pex_venv.pip_install("pex==2.1.20", *requirements)
        pex_venv.pip_wheel("-v", "--cache-dir", wheels, "--wheel-dir", wheels, *requirements)
        entry_points = pex_venv.find_entry_points(pspec)
        if entry_points:
            wheel_path = pspec.find_wheel(wheels)
            result = []
            for name in entry_points:
                target = os.path.join(dist_folder, name)
                runez.delete(target, logger=False)
                pex_venv.run_python(
                    "-mpex", "-v", "-o%s" % target, "--pex-root", pex_root, "--tmpdir", tmp,
                    "--no-index", "--find-links", wheels,  # resolver options
                    None if compile else "--no-compile",  # output options
                    "-c%s" % name,  # entry point options
                    "--python-shebang", "/usr/bin/env python%s" % pspec.python.major,
                    wheel_path,
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
        if pspec.folder:
            args = [pspec.folder]

        elif pspec.dashed == PICKLEY:
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
    def package(pspec, build_folder, dist_folder, requirements, compile):
        runez.ensure_folder(dist_folder, clean=True, logger=False)
        venv = PythonVenv(pspec, folder=dist_folder)
        venv.pip_install(*requirements)
        if compile:
            venv.run_python("-mcompileall", dist_folder)

        entry_points = venv.find_entry_points(pspec)
        if entry_points:
            result = []
            for name in entry_points:
                result.append(venv.bin_path(name))

            return result
