import os
import time

from pickley import system


class SoftLockException(Exception):
    """Raised when soft lock can't be acquired"""

    def __init__(self, folder):
        self.folder = folder


class SoftLock:
    """
    Simple soft file lock mechanism

    Several pickley processes could be attempting to auto upgrade a package at the same time
    With this class, we provide a soft lock mechanism on folders:
    - first process "grabs a lock" (lock based on existence of file, and its age)
    - lock consists of creating a <folder>.lock file, pid of process that created the file is stored there
    - a timeout of > 0 can be used to wait for lock acquisition
    - a timeut of 0 will make it so that calling process fails to obtain lock immediately (via SoftLockException)
    - a lock can be held only for the given 'invalid' time (allows to not get blocked by a crashed left-over)
    - the created folder is kept around for 'keep' days (if > 0)
    """

    def __init__(self, folder, timeout=0, invalid=10, keep=0):
        """
        :param str folder: Folder to lock access to
        :param int timeout: Timeout in minutes after which to abort if lock could not be acquired
        :param int invalid: Age in minutes after which to consider existing lock as invalid
        :param int keep: Age in days for which to keep the folder around
        """
        self.folder = folder
        self.lock = self.folder + ".lock"
        self.timeout = timeout * 60
        self.invalid = invalid * 60
        self.keep = keep * 60 * 60 * 24

    def _locked(self):
        """
        :return bool: True if lock is held by another process
        """
        if not system.file_younger(self.lock, self.invalid):
            # Lock file does not exist or invalidation age reached
            return False

        # Consider locked if pid stated in lock file is still valid
        pid = system.to_int(system.first_line(self.lock))
        return system.check_pid(pid)

    def _should_keep(self):
        """Should we keep folder after lock release?"""
        return system.file_younger(self.folder, self.keep)

    def __enter__(self):
        """
        Acquire lock
        """
        cutoff = time.time() + self.timeout
        while self._locked():
            if time.time() >= cutoff:
                raise SoftLockException(self.folder)
            time.sleep(1)

        # We got the soft lock
        system.write_contents(self.lock, "%s\n" % os.getpid())

        if not self._should_keep():
            system.delete_file(self.folder, quiet=not self.keep)

        return self

    def __exit__(self, *_):
        """
        Release lock
        """
        if not self._should_keep():
            system.delete_file(self.folder, quiet=not self.keep)
        system.delete_file(self.lock, quiet=True)


def vrun(package_name, *args, **kwargs):
    """
    Run package_name + args from an on-the-fly create virtualenv, with 'package_name' auto-installed into it.
    This allows us to run commands like 'pex ...' with pex installed when/if needed

    :param str package_name: Virtualized command to run (pip, pex, etc...)
    :param args: Command line args
    :param kwargs: Optional named args to pass-through to system.run_program()
    """
    folder = system.SETTINGS.meta.full_path(".v")
    with SoftLock(folder, timeout=system.SETTINGS.install_timeout, invalid=system.SETTINGS.install_timeout, keep=10) as lock:
        shared = SharedVenv(lock)
        return shared._run_from_venv(package_name, *args, **kwargs)


class SharedVenv:
    def __init__(self, lock):
        """
        :param SoftLock lock: Acquired lock
        """
        self.lock = lock
        self.folder = lock.folder
        self.bin = os.path.join(self.folder, "bin")
        self.python = os.path.join(self.bin, "python")
        self.pip = os.path.join(self.bin, "pip")
        if system.file_younger(self.python, self.lock.keep):
            return
        system.delete_file(self.folder)
        venv = system.virtualenv_path()
        if not venv:
            system.abort("Can't determine path to virtualenv.py")
        system.run_program(system.PYTHON, venv, self.folder)

    def _pip_install(self, *args, **kwargs):
        """Run 'pip install' with given args"""
        self._run_from_venv("pip", "install", "-i", system.SETTINGS.index, *args, **kwargs)

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
        program = os.path.join(self.bin, program)
        self._install_module(package_name, program)
        kwargs["shorten"] = self.bin
        return system.run_program(program, *args, **kwargs)
