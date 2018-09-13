import os
import time

from pickley import short, system
from pickley.settings import SETTINGS


class WorkingVenv:
    """
    Auto-create and manage a venv to run pip, wheel, pex etc from
    Access is protected via a soft file lock
    """
    def __init__(self, timeout=5, max_lock_age=10, max_venv_age=10):
        """
        :param int timeout: Timeout in minutes after which to abort if venv lock could not be acquired
        :param int max_lock_age: Age in minutes after which to consider existing lock as invalid
        :param int max_venv_age: Age in days after which to automatically recreate the venv
        """
        self.timeout = timeout * 60
        self.max_lock_age = max_lock_age * 60
        self.max_venv_age = max_venv_age * 60 * 60
        self.folder = SETTINGS.meta.full_path(".v")
        self.lock = SETTINGS.meta.full_path(".v.pid")
        self.bin_folder = os.path.join(self.folder, "bin")
        self.python = os.path.join(self.bin_folder, "python")
        self.pip = os.path.join(self.bin_folder, "pip")

    def _ensure_venv(self):
        """Ensure that venv is installed, or recreated if need be"""
        if system.file_younger(self.python, self.max_venv_age):
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
    def run(cls, package_name, *args, **kwargs):
        with WorkingVenv() as venv:
            return venv._run_from_venv(package_name, *args, **kwargs)

    @classmethod
    def create_venv(cls, folder, python=None):
        """
        :param str folder: Create a venv in 'folder'
        :param str|None python: Python interpreter to use (defaults to python)
        """
        return cls.run("virtualenv", "--python", python, folder)

    def _locked(self):
        """
        :return bool: True if lock is held by another process
        """
        if system.file_younger(self.lock, self.max_lock_age):
            # File exists and invalidation age not reached
            return True

        # Locked if pid stated in lock file is still valid
        pid = system.to_int(system.first_line(self.lock))
        return system.check_pid(pid)

    def _pip_install(self, *args, **kwargs):
        """Run 'pip install' with given args"""
        self._run_from_venv("pip", "install", "-i", SETTINGS.index, *args, **kwargs)

    def _install_module(self, package_name, program):
        """
        :param str package_name: Pypi module to install in venv, if not already installed
        :param str program: Full path to corresponding executable in this venv
        """
        if system.DRYRUN or system.is_executable(program):
            return
        self._pip_install(package_name)

    def _run_from_venv(self, package_name, *args, **kwargs):
        """
        Should be called while holding the soft file lock in context only

        :param str package_name: Pypi package to which command being ran belongs to
        :param args: Args to invoke program with
        :param kwargs: Additional args, use program= if entry point differs from 'package_name'
        """
        args = system.flattened(args, unique=False)
        program = kwargs.pop("program", package_name)
        program = os.path.join(self.bin_folder, program)
        self._install_module(package_name, program)
        kwargs["base"] = [self.bin_folder, SETTINGS.meta.path]
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
