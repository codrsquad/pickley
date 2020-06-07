import logging
import os
import re

import runez

from pickley import abort
from pickley.delivery import DeliveryMethod


LOG = logging.getLogger(__name__)
RE_BIN_SCRIPT = re.compile(r"^[./]+/bin/([-a-z0-9_.]+)$", re.IGNORECASE)


def entry_points_from_txt(path):
    metadata = runez.file.ini_to_dict(path, default={})
    return metadata.get("console_scripts")


def entry_points_from_metadata(path):
    metadata = runez.read_json(path, default={})
    return metadata.get("extensions", {}).get("python.commands", {}).get("wrap_console")


def first_line(path):
    """str: First line of file with 'path', if any"""
    for line in runez.readlines(path, default=[], errors="ignore"):
        return line


class PythonVenv(object):
    def __init__(self, folder, python, index):
        """
        Args:
            folder (str): Target folder (empty string for testing, venv is not actually created in that case)
            python (pickley.env.PythonInstallation): Python to use
            index (str | None): Optional custom pypi index to use
        """
        self.folder = folder
        self.python = python
        self.index = index
        self.py_path = self.bin_path("python")
        if folder:
            if python.problem:
                abort("Python '%s' is not usable: %s" % (runez.bold(python), runez.red(python.problem)))

            runez.ensure_folder(folder, clean=True)
            if python.needs_virtualenv:
                import virtualenv

                vpath = virtualenv.__file__
                if vpath.endswith(".pyc"):
                    vpath = vpath[:-1]

                cmd = [python.executable, vpath]
                if not python.is_invoker:  # pragma: no cover, when pickley install with py2...
                    cmd.append("-p")
                    cmd.append(python.executable)

                cmd.append(folder)
                with runez.Anchored(os.path.dirname(vpath)):
                    runez.run(*cmd)

            else:
                python.run("-mvenv", folder)

    def bin_path(self, name):
        """
        Args:
            name (str): File name

        Returns:
            (str): Full path to this <venv>/bin/<name>
        """
        return os.path.join(self.folder, "bin", name)

    def find_entry_points(self, pspec):
        """
        Args:
            pspec (pickley.PackageSpec): Package spec to look for

        Returns:
            (dict | None): Entry points, when available
        """
        if runez.DRYRUN:
            return {pspec.dashed: "dryrun"}  # Pretend an entry point exists in dryrun mode

        r = self.run_python("-mpip", "show", "-f", pspec.dashed, fatal=False, logger=False)
        if r.succeeded:
            expected_shebang = "#!%s" % runez.quoted(os.path.dirname(self.py_path), adapter=None)
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
                            if runez.is_executable(path):
                                shebang = first_line(path)
                                if shebang.startswith(expected_shebang):
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

    def get_shebang(self, wheels):
        """For pex: determine most general shebang to use"""
        shebang = "/usr/bin/env python"
        if any(n.endswith(".whl") and not n.endswith("-py2.py3-none-any.whl") for n in os.listdir(wheels)):
            shebang += str(self.python.major)

        return shebang

    def pip_install(self, *args, **kwargs):
        """Allows to not forget to state the -i index..."""
        return self._run_pip("install", "-i", self.index, *args, **kwargs)

    def pip_wheel(self, *args, **kwargs):
        """Allows to not forget to state the -i index..."""
        return self._run_pip("wheel", "-i", self.index, *args, **kwargs)

    def run_python(self, *args, **kwargs):
        """Run python from this venv with given args"""
        return runez.run(self.py_path, *args, **kwargs)

    def _run_pip(self, *args, **kwargs):
        return self.run_python("-mpip", "-v", *args, **kwargs)


class Packager:
    """Ancestor to package/install implementations"""

    @staticmethod
    def install(pspec):
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
        pex_venv = PythonVenv(os.path.join(build_folder, "pex-venv"), pspec.python, pspec.index)
        pex_venv.pip_install("wheel", "pex==1.6.7", *requirements)
        pex_venv.pip_wheel("-v", "--cache-dir", wheels, "--wheel-dir", wheels, *requirements)
        entry_points = pex_venv.find_entry_points(pspec)
        if entry_points:
            result = []
            for name in entry_points:
                target = os.path.join(dist_folder, name)
                runez.delete(target, logger=False)
                pex_venv.run_python(
                    "-mpex", "-v", "--no-pypi", "--pre", "--cache-dir", wheels, "-f", wheels,
                    "-c%s" % name, "-o%s" % target, name,
                    "--python-shebang", pex_venv.get_shebang(wheels),
                )
                result.append(target)

            return result


class VenvPackager(Packager):
    """Install in a virtualenv"""

    @staticmethod
    def install(pspec):
        assert pspec.version
        delivery = DeliveryMethod.delivery_method_by_name(pspec.settings.delivery)
        target = pspec.install_path
        venv = PythonVenv(target, pspec.python, pspec.index)
        venv.pip_install(pspec.specced)
        entry_points = venv.find_entry_points(pspec)
        if not entry_points:
            runez.delete(pspec.meta_path)
            abort("Can't install '%s', it is %s" % (runez.bold(pspec.dashed), runez.red("not a CLI")))

        return delivery.install(pspec, venv, entry_points)

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements):
        runez.ensure_folder(dist_folder, clean=True)
        venv = PythonVenv(dist_folder, pspec.python, pspec.index)
        venv.pip_install(*requirements)
        entry_points = venv.find_entry_points(pspec)
        if entry_points:
            result = []
            for name in entry_points:
                result.append(venv.bin_path(name))

            return result
