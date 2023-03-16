import logging
import os

import runez

from pickley import abort, PackageSpec, PICKLEY
from pickley.bstrap import create_virtualenv, pip_version
from pickley.delivery import DeliveryMethod


LOG = logging.getLogger(__name__)


class PackageFolder:
    """Allows to track reported file contents by `pip show -f`"""

    def __init__(self, location=None, folder=None):
        self.location = location
        self.folder = folder
        self.files = {}

    def __str__(self):
        return f"{self.folder} [{len(self)} files]"

    def __len__(self):
        return len(self.files)

    def add_file(self, path):
        if not self.location:
            self.location = os.path.dirname(path)

        if self.folder is None:
            self.folder = os.path.basename(os.path.dirname(path))

        relative = path[len(self.location) + 1:]
        self.files[relative] = path


class PackageContents:
    """Contents of a pip-installed package"""

    def __init__(self, venv):
        """
        Args:
            venv (PythonVenv): Venv to extract info from
        """
        self.venv = venv
        self.location = None
        self.bin = PackageFolder(folder="bin")
        self.completers = PackageFolder(folder="bin")
        self.dist_info = PackageFolder()
        self.files = None
        self.info = {}
        name = venv.pspec.dashed
        if runez.DRYRUN and not runez.is_executable("bin/pip"):
            self.bin.files = {venv.pspec.dashed: "dryrun"}  # Pretend an entry point exists in dryrun mode
            return

        if name == PICKLEY:
            self.bin.files = {name: name}  # When pickley is installed with -e (--editable), metadata is not "standard"
            return

        if name == "ansible":
            # Is there a better way to detect weird indirections like ansible does?
            version = venv.pspec.desired_track.version
            name = "ansible-core" if not version or version >= "4" else "ansible-base"

        r = venv.run_pip("show", "-f", name, fatal=False)
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

                    elif runez.is_executable(path):
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
        return f"{self.venv.pspec} [{runez.short(self.location)}]"

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
                        nb = runez.plural(wrap_console, "entry point")
                        runez.log.trace(f"Found {nb} in metadata.json")
                        return wrap_console

        entry_points_txt = self.dist_info.files.get("entry_points.txt")
        if entry_points_txt:
            metadata = runez.file.ini_to_dict(entry_points_txt)
            console_scripts = metadata.get("console_scripts")
            if console_scripts:
                nb = runez.plural(console_scripts, "entry point")
                runez.log.trace(f"Found {nb} in entry_points.txt")
                return console_scripts

        if self.bin.files:
            nb = runez.plural(self.bin.files, "bin/ script")
            runez.log.trace(f"Found {nb}")

        return self.bin.files or None


class PythonVenv:

    _vv_fallback = "20.16.1"

    def __init__(self, folder, pspec, create=True):
        """
        Args:
            folder (str | pathlib.Path): Target folder
            pspec (pickley.PackageSpec): Package spec to install
            create (bool): Create venv if True
        """
        self.folder = folder
        self.pspec = pspec
        self.python = pspec.python or pspec.cfg.find_python(pspec)
        if create:
            self._create_virtualenv()

    def __repr__(self):
        return runez.short(self.folder)

    def _create_virtualenv(self, runner=runez.run):
        runez.ensure_folder(self.folder, clean=True, logger=False)
        vv = self.pspec.cfg.get_virtualenv(self.pspec)
        if not vv and runez.SYS_INFO.platform_id.is_macos and runez.SYS_INFO.platform_id.arch != "arm64":
            # Weird bug reported on Intel macs, where tox installed via -mvenv fails to create its own .tox venvs in a mysterious way
            vv = self._vv_fallback

        if vv and vv != "no":
            return self._old_virtualenv(runner, vv)

        r = runez.run(self.python.executable, "-mvenv", self.folder, fatal=False)
        if r.failed or (not runez.DRYRUN and not self.pip_path):
            return self._old_virtualenv(runner, "latest")

        pip_spec = pip_version(self.python.version.components)
        pip_spec = f"pip=={pip_spec}" if pip_spec else "pip"
        return self.run_pip("install", "-U", pip_spec)

    def _old_virtualenv(self, runner, vv):
        """Create a virtualenv using old virtualenv module"""
        pv = self.python.version.components
        if not vv or vv == "latest":
            vv = PackageSpec(self.pspec.cfg, "virtualenv")
            vv = vv.desired_track.version

        tmp = self.pspec.cfg.cache.path
        runez.ensure_folder(self.folder, clean=True, logger=False)
        return create_virtualenv(tmp, pv, self.python.executable, self.folder, virtualenv_version=vv, runner=runner, dryrun=runez.DRYRUN)

    @property
    def pip_path(self):
        return self.bin_path("pip", try_variant=True)

    def bin_path(self, name, try_variant=False):
        """
        Args:
            name (str): File name

        Returns:
            (str): Full path to this <venv>/bin/<name>
        """
        path = os.path.join(self.folder, "bin", name)
        if runez.DRYRUN or os.path.exists(path):
            return path

        if try_variant:
            path = os.path.join(self.folder, "bin", f"{name}3")
            if os.path.exists(path):
                return path

    def pip_install(self, *args):
        """Allows to not forget to state the -i index..."""
        r = self.run_pip("install", "-i", self.pspec.index, *args, fatal=False)
        if r.failed:
            message = "\n".join(simplified_pip_error(r.error, r.output))
            abort(message)

        return r

    def pip_wheel(self, *args):
        """Allows to not forget to state the -i index..."""
        return self.run_pip("wheel", "-i", self.pspec.index, *args)

    def run_pip(self, *args, **kwargs):
        return runez.run(self.pip_path, *args, **kwargs)

    def run_python(self, *args, **kwargs):
        """Run python from this venv with given args"""
        # kwargs.setdefault("short_exe", True)
        exe = self.bin_path("python", try_variant=True)
        return runez.run(exe, *args, **kwargs)


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
            requirements (list): Additional requirements (same convention as pip, can be package names or package specs)
            run_compile_all (bool): Call 'compileall' on generated package?

        Returns:
            (list | None): List of packaged executables
        """
        raise NotImplementedError("Packaging with packager '{packager}' is not supported")


class PexPackager(Packager):
    """Package via pex (https://pypi.org/project/pex/)"""

    @staticmethod
    def install(pspec, ping=True, no_binary=None):
        raise NotImplementedError("Installation with 'PexPackager' is not supported")

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        runez.ensure_folder(build_folder, clean=True)
        runez.abort_if(pspec.python.major < 3, "Packaging with pex is not supported any more with python2")
        pex_root = os.path.join(build_folder, "pex-root")
        tmp = os.path.join(build_folder, "pex-tmp")
        wheels = os.path.join(build_folder, "wheels")
        runez.ensure_folder(tmp, logger=False)
        runez.ensure_folder(wheels, logger=False)
        pex_venv = PythonVenv(os.path.join(build_folder, "pex-venv"), pspec)
        pex_venv.pip_install("pex==2.1.102", *requirements)
        pex_venv.pip_wheel("--cache-dir", wheels, "--wheel-dir", wheels, *requirements)
        contents = PackageContents(pex_venv)
        if contents.entry_points:
            wheel_path = pspec.find_wheel(wheels)
            result = []
            for name in contents.entry_points:
                target = os.path.join(dist_folder, name)
                runez.delete(target)
                pex_venv.run_python(
                    "-mpex", f"-o{target}", "--pex-root", pex_root, "--tmpdir", tmp,
                    "--no-index", "--find-links", wheels,  # resolver options
                    None if run_compile_all else "--no-compile",  # output options
                    f"-c{name}",  # entry point options
                    "--python-shebang", f"/usr/bin/env python{pspec.python.major}",
                    wheel_path,
                )
                result.append(target)

            return result


class VenvPackager(Packager):
    """Install in a virtualenv"""

    @staticmethod
    def install(pspec, ping=True, no_binary=None):
        delivery = DeliveryMethod.delivery_method_by_name(pspec.settings.delivery)
        delivery.ping = ping
        args = []
        if no_binary:
            args.append("--no-binary")
            args.append(no_binary)

        venv_folder = pspec.get_install_path(pspec.desired_track.version)
        if pspec.folder:
            args.append(pspec.folder)

        elif pspec._pickley_dev_mode:
            args.append("-e")
            args.append(pspec._pickley_dev_mode)

        else:
            args.append(f"{pspec.dashed}=={pspec.desired_track.version}")

        venv = PythonVenv(venv_folder, pspec)
        venv.pip_install(*args)
        contents = PackageContents(venv)
        if not contents.entry_points:
            runez.delete(pspec.meta_path)
            abort(f"Can't install '{runez.bold(pspec.dashed)}', it is {runez.red('not a CLI')}")

        return delivery.install(venv, contents.entry_points)

    @staticmethod
    def package(pspec, build_folder, dist_folder, requirements, run_compile_all):
        runez.ensure_folder(dist_folder, clean=True, logger=False)
        venv = PythonVenv(dist_folder, pspec)
        for requirement_file in requirements.requirement_files:
            venv.pip_install("-r", requirement_file)
        if requirements.additional_packages:
            venv.pip_install(*requirements.additional_packages)
        venv.pip_install(requirements.project)
        if run_compile_all:
            venv.run_python("-mcompileall", dist_folder)

        contents = PackageContents(venv)
        if contents.entry_points:
            result = []
            for name in contents.entry_points:
                result.append(venv.bin_path(name))

            return result
