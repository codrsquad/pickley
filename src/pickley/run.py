import os
import time

from pickley import short, system
from pickley.settings import SETTINGS


def add_paths(result, env_var, *paths):
    """
    :param dict result: Where to add path customization
    :param str env_var: Env var to customize
    :param list *paths: Paths to add, if corresponding folder exists
    """
    added = 0
    current = os.environ.get(env_var, "")
    if current:
        current = current.split(":")
    else:
        current = []
    current = [x for x in current if x]
    for path in paths:
        if os.path.isdir(path) and path not in current:
            added += 1
            current.append(path)
    if added:
        result[env_var] = ":".join(current)


class WorkingVenv:
    """
    Auto-create and manage a venv to run pip, wheel, pex etc from
    Access is protected via a soft file lock
    """
    def __init__(self, timeout=5, max_lock_age=10, max_venv_age=10, env=None):
        """
        :param int timeout: Timeout in minutes after which to abort if venv lock could not be acquired
        :param int max_lock_age: Age in minutes after which to consider existing lock as invalid
        :param int max_venv_age: Age in days after which to automatically recreate the venv
        :param dict|None env: Customize PATH-like env vars when provided
        """
        self.timeout = timeout * 60
        self.max_lock_age = max_lock_age * 60
        self.max_venv_age = max_venv_age * 60 * 60
        self.env = env
        self.folder = SETTINGS.meta.full_path(".venv")
        self.lock = SETTINGS.meta.full_path(".venv.pid")
        self.bin_folder = os.path.join(self.folder, "bin")
        self.python = os.path.join(self.bin_folder, "python")
        self.pip = os.path.join(self.bin_folder, "pip")

    def _ensure_venv(self):
        """Ensure that venv is installed, or recreated if need be"""
        age = system.file_age(self.python)
        if age is not None and age < self.max_venv_age:
            return
        system.delete_file(self.folder)
        venv = self._virtualenv_path()
        if not venv:
            return system.abort("Can't determine path to virtualenv.py")
        return system.run_program(system.PYTHON, venv, self.folder)

    def _virtualenv_path(self):
        """
        :return str: Path to our own virtualenv.py
        """
        import virtualenv
        path = virtualenv.__file__
        if path and path.endswith(".pyc"):
            path = path[:-1]
        return path

    @classmethod
    def create_venv(cls, folder, python=None):
        """
        :param str folder: Create a venv in 'folder'
        :param str|None python: Python interpreter to use (defaults to python)
        """
        with WorkingVenv() as venv:
            return venv.run("virtualenv", "--python", python, folder)

    def _locked(self):
        """
        :return bool: True if lock is held by another process
        """
        age = system.file_age(self.lock)
        if age is None or age > self.max_lock_age:
            # File does not exist or too long since lock was acquired
            return False

        # Locked if pid stated in lock file is still valid
        pid = system.to_int(system.first_line(self.lock))
        return system.check_pid(pid)

    def _pip_install(self, *args, **kwargs):
        """Run 'pip install' with given args"""
        self.run("pip", "install", "-i", SETTINGS.index, *args, **kwargs)

    def _install_module(self, package_name, program):
        """
        :param str package_name: Pypi module to install in venv, if not already installed
        :param str program: Full path to corresponding executable in this venv
        """
        if system.is_executable(program):
            return
        self._pip_install(package_name)

    def run(self, package_name, *args, **kwargs):
        """
        Should be called while holding the soft file lock in context only

        :param str package_name: Pypi package to which command being ran belongs to
        :param args: Args to invoke program with
        :param kwargs: Additional args, use program= if entry point differs from 'package_name'
        """
        program = kwargs.pop("program", package_name)
        program = os.path.join(self.bin_folder, program)
        self._install_module(package_name, program)
        kwargs["base"] = {self.bin_folder, SETTINGS.meta.path}
        return system.run_program(program, *args, **kwargs)

    def __enter__(self):
        """
        Auto-create venv, with a soft file lock while we're running in context
        """
        cutoff = time.time() + self.timeout
        while self._locked():
            if time.time() > cutoff:
                system.abort("Could not obtain lock on %s, please try again later" % short(self.folder))
            time.sleep(1)

        # We got the soft lock
        system.write_contents(self.lock, "%s\n" % os.getpid())
        self._ensure_venv()

        return self

    def __exit__(self, *_):
        """
        Release lock on venv
        """
        system.delete_file(self.lock)


class Runner:
    def __init__(self, cache):
        """
        :param str cache: Path to folder to use as cache
        """
        self.name = self.__class__.__name__.replace("Runner", "").lower()
        self.cache = cache

    def run(self, *args):
        args = system.flattened([self.prelude_args(), args], unique=False)

        if system.DRYRUN:
            system.debug("Would run: %s %s", self.name, system.represented_args(args))
            return None

        system.ensure_folder(self.cache, folder=True)
        with WorkingVenv(env=self.custom_env()) as venv:
            return venv.run(self.name, *args)

    def custom_env(self):
        """
        :return dict: Optional customized env vars to use
        """

    def prelude_args(self):
        """
        :return list|None: Arguments to pass to invoked module for all invocations
        """


class PipRunner(Runner):
    def prelude_args(self):
        """
        :return list|None: Arguments to pass to invoked module for all invocations
        """
        return ["--cache-dir", self.cache]

    def wheel(self, *package_names):
        return self.run("wheel", "-i", SETTINGS.index, "--wheel-dir", self.cache, *package_names)


class PexRunner(Runner):
    def custom_env(self):
        """
        :return dict: Optional customized env vars to use
        """
        result = {}
        add_paths(result, "PKG_CONFIG_PATH", "/usr/local/opt/openssl/lib/pkgconfig")
        return result

    def prelude_args(self):
        """
        :return list|None: Arguments to pass to invoked module for all invocations
        """
        return ["--no-pypi", "--cache-dir", self.cache, "--repo", self.cache]

    def is_universal(self, package_name, version):
        """
        :param str package_name: Pypi package name
        :param str version: Specific version of 'package_name' to examine
        :return bool: True if wheel exists and is universal
        """
        if not os.path.isdir(self.cache):
            return False
        prefix = "%s-%s-" % (package_name, version)
        for fname in os.listdir(self.cache):
            if fname.startswith(prefix) and fname.endswith(".whl"):
                return "py2.py3-none" in fname
        return False

    def resolved_python(self, package_name):
        """
        :param str package_name: Pypi package name
        :return pickley.settings.Definition: Associated definition
        """
        return SETTINGS.resolved_definition("python", package_name=package_name)

    def build(self, script_name, package_name, version, destination):
        """
        :param str script_name: Entry point name
        :param str package_name: Pypi package name
        :param str version: Specific version of 'package_name' to use
        :param str destination: Path where to generate pex
        :return str|None: None if successful, problem description otherwise
        """
        system.delete_file(destination)
        python = self.resolved_python(package_name)
        args = ["-c%s" % script_name, "-o%s" % destination, "%s==%s" % (package_name, version)]

        # Note: 'python.source' being 'SETTINGS.defaults' is the same as it being 'system.PYTHON'
        # Writing it this way is easier to change in tests
        explicit_python = python and python.value and python.source is not SETTINGS.defaults
        if explicit_python:
            shebang = python.value
            args.append("--python=%s" % python.value)

        elif not python or self.is_universal(package_name, version):
            shebang = "python"

        else:
            shebang = python.value

        if shebang:
            if not os.path.isabs(shebang):
                shebang = "/usr/bin/env %s" % shebang
            args.append("--python-shebang=%s" % shebang)

        return self.run(args)
