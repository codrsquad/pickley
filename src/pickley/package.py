import logging
import os

import runez

from pickley import abort, PICKLEY
from pickley.delivery import DeliveryMethod


LOG = logging.getLogger(__name__)


def download_command(target, url):
    curl = runez.which("curl")
    if curl:
        return [curl, "-s", "-o", target, url]

    return ["wget", "-q", "-O%s" % target, url]


class PackageFolder(object):
    """Allows to track reported file contents by `pip show -f`"""

    def __init__(self, location=None, folder=None):
        self.location = location
        self.folder = folder
        self.files = {}

    def __str__(self):
        return "%s [%s files]" % (self.folder, len(self))

    def __len__(self):
        return len(self.files)

    def add_file(self, path):
        if not self.location:
            self.location = os.path.dirname(path)

        if self.folder is None:
            self.folder = os.path.basename(os.path.dirname(path))

        relative = path[len(self.location) + 1:]
        self.files[relative] = path


class PackageContents(object):
    """Contents of a pip-installed package"""

    def __init__(self, venv, pspec):
        """
        Args:
            venv (PythonVenv): Venv to extract info from
            pspec (pickley.PackageSpec): Package spec to look for
        """
        self.venv = venv
        self.pspec = pspec
        self.location = None
        self.bin = PackageFolder(folder="bin")
        self.completers = PackageFolder(folder="bin")
        self.dist_info = PackageFolder()
        self.files = None
        self.info = {}
        name = pspec.dashed
        if runez.DRYRUN and not venv.is_venv_exe("bin/pip"):
            self.bin.files = {pspec.dashed: "dryrun"}  # Pretend an entry point exists in dryrun mode
            return

        if name == "ansible":
            # Is there a better way to detect weird indirections like ansible does?
            name = "ansible-core" if not pspec.version or pspec.version >= "4" else "ansible-base"

        r = venv.run_python("-mpip", "show", "-f", name, fatal=False)
        if not r.succeeded:
            return

        for line in r.output.splitlines():
            line = line.strip()
            if not line:
                continue

            if self.files is not None:
                dirname, basename = os.path.split(line)
                if "__pycache__" in dirname or basename.endswith(".pyc"):
                    continue

                path = os.path.abspath(os.path.join(self.location, line))
                if os.path.basename(dirname) == "bin":
                    if "_completer" in basename:
                        self.completers.add_file(path)

                    elif venv.is_venv_exe(path):
                        self.bin.add_file(path)

                elif dirname.endswith(".dist-info"):
                    self.dist_info.add_file(path)

                else:
                    self.files.add_file(path)

            elif line.startswith("Files:"):
                if not self.location:
                    return

                self.files = PackageFolder(runez.resolved_path(self.location), folder="")

            else:
                key, _, value = line.partition(":")
                value = value.strip()
                self.info[key] = value
                if key == "Location":
                    self.location = value

    def __repr__(self):
        return "%s [%s]" % (self.pspec, runez.short(self.location))

    @runez.cached_property
    def entry_points(self):
        metadata_json = self.dist_info.files.get("metadata.json")
        if metadata_json:
            metadata = runez.read_json(metadata_json, default={})
            extensions = metadata.get("extensions")
            if isinstance(extensions, dict):
                commands = extensions.get("python.commands")
                if isinstance(commands, dict):
                    wrap_console = commands.get("wrap_console")
                    if wrap_console:
                        runez.log.trace("Found %s entry points in metadata.json" % len(wrap_console))
                        return wrap_console

        entry_points_txt = self.dist_info.files.get("entry_points.txt")
        if entry_points_txt:
            metadata = runez.file.ini_to_dict(entry_points_txt, default={})
            console_scripts = metadata.get("console_scripts")
            if console_scripts:
                runez.log.trace("Found %s entry points in entry_points.txt" % len(console_scripts))
                return console_scripts

        if self.bin.files:
            runez.log.trace("Found %s bin/ scripts" % len(self.bin.files))

        return self.bin.files or None


class PythonVenv(object):
    def __init__(self, pspec=None, folder=None, python=None, index=None, cfg=None):
        """
        Args:
            pspec (pickley.PackageSpec | None): Package spec to install
            folder (str | None): Target folder (default: pspec.install_path)
            python (pickley.env.PythonInstallation): Python to use (default: pspec.python)
            index (str | None): Optional custom pypi index to use (default: pspec.index)
            cfg (pickley.PickleyConfig | None): Config to use
        """
        if folder is None and pspec:
            folder = pspec.install_path

        if not python and pspec:
            python = pspec.python

        if not index and pspec:
            index = pspec.index

        self.folder = folder
        self.index = index
        self.py_path = os.path.join(folder, "bin", "python")
        if folder:
            cfg = cfg or pspec.cfg
            python = python or cfg.find_python(pspec=pspec)
            runez.ensure_folder(folder, clean=True, logger=False)
            if cfg.bundled_virtualenv_path:
                runez.run(cfg.bundled_virtualenv_path, "-p", python.executable, folder)
                return

            if python.major > 2 and (not pspec or pspec.dashed != "tox"):
                # See https://github.com/tox-dev/tox/issues/1689
                r = runez.run(python.executable, "-mvenv", self.folder, fatal=False)
                if r.succeeded:
                    pip = "pip"
                    if os.environ.get("VIRTUALENV_PIP"):  # pragma: no cover
                        # Optionally respect https://tox.readthedocs.io/en/latest/config.html#conf-download
                        pip += "==%s" % os.environ.get("VIRTUALENV_PIP")

                    self.pip_install("-U", pip, "setuptools", "wheel")
                    return

                LOG.debug("Module venv failed, trying virtualenv bootstrap")  # pragma: no cover

            runez.ensure_folder(cfg.cache.path)
            zipapp = os.path.realpath(cfg.cache.full_path("virtualenv.pyz"))
            if not runez.file.is_younger(zipapp, runez.date.SECONDS_IN_ONE_DAY):
                runez.delete(zipapp, fatal=False)
                args = download_command(zipapp, "https://bootstrap.pypa.io/virtualenv/virtualenv.pyz")
                runez.run(*args)

            runez.run(python.executable, zipapp, folder)

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

    def is_venv_exe(self, path):
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
        return runez.run(self.py_path, *args, **kwargs)

    def _run_pip(self, *args, **kwargs):
        return self.run_python("-mpip", "--isolated", *args, **kwargs)


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
            ping (bool): If True, touch .ping file when done
        """

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        """Package current folder

        Args:
            pspec (pickley.PackageSpec): Targeted package spec
            build_folder (str): Folder to use as build cache
            dist_folder (str): Folder where to produce package
            requirements (list): Additional requirements (same convention as pip, can be package names or package specs)
            run_compile_all (bool): Call 'compileall' on generated package?

        Returns:
            (list | None): List of packaged executables
        """
        raise NotImplementedError("Packaging with packager '{packager}' is not supported")


class PexPackager(Packager):
    """Package via pex (https://pypi.org/project/pex/)"""

    @staticmethod
    def install(pspec, ping=True):  # pragma: no cover
        raise NotImplementedError("Installation with 'PexPackager' is not supported")

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        runez.ensure_folder(build_folder, clean=True)
        if pspec.python.major < 3:  # pragma: no cover
            abort("Packaging with pex is not supported any more with python2")

        pex_root = os.path.join(build_folder, "pex-root")
        tmp = os.path.join(build_folder, "pex-tmp")
        wheels = os.path.join(build_folder, "wheels")
        runez.ensure_folder(tmp, logger=False)
        runez.ensure_folder(wheels, logger=False)
        pex_venv = PythonVenv(pspec, folder=os.path.join(build_folder, "pex-venv"))
        pex_venv.pip_install("pex==2.1.42", *requirements)
        pex_venv.pip_wheel("--cache-dir", wheels, "--wheel-dir", wheels, *requirements)
        contents = PackageContents(pex_venv, pspec)
        if contents.entry_points:
            wheel_path = pspec.find_wheel(wheels)
            result = []
            for name in contents.entry_points:
                target = os.path.join(dist_folder, name)
                runez.delete(target)
                pex_venv.run_python(
                    "-mpex", "-o%s" % target, "--pex-root", pex_root, "--tmpdir", tmp,
                    "--no-index", "--find-links", wheels,  # resolver options
                    None if run_compile_all else "--no-compile",  # output options
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
            venv.pip_install(*extras)

        contents = PackageContents(venv, pspec)
        if not contents.entry_points:
            runez.delete(pspec.meta_path)
            abort("Can't install '%s', it is %s" % (runez.bold(pspec.dashed), runez.red("not a CLI")))

        return delivery.install(pspec, venv, contents.entry_points)

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        runez.ensure_folder(dist_folder, clean=True, logger=False)
        venv = PythonVenv(pspec, folder=dist_folder)
        venv.pip_install(*requirements)
        if run_compile_all:
            venv.run_python("-mcompileall", dist_folder)

        contents = PackageContents(venv, pspec)
        if contents.entry_points:
            result = []
            for name in contents.entry_points:
                result.append(venv.bin_path(name))

            return result
