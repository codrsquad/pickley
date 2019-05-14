import logging
import os
import time

import runez

from pickley import system


LOG = logging.getLogger(__name__)


class SoftLockException(Exception):
    """Raised when soft lock can't be acquired"""

    def __init__(self, folder):
        self.folder = folder


class SoftLock(object):
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
        :param int|float timeout: Timeout in minutes after which to abort if lock could not be acquired
        :param int|float invalid: Age in minutes after which to consider existing lock as invalid
        :param int|float keep: Age in days for which to keep the folder around
        """
        self.folder = folder
        self.lock = self.folder + ".lock"
        self.timeout = timeout * 60
        self.invalid = invalid * 60
        self.keep = keep * 60 * 60 * 24

    def __repr__(self):
        return self.lock

    def _locked(self):
        """
        :return bool: True if lock is held by another process
        """
        if not runez.is_younger(self.lock, self.invalid):
            # Lock file does not exist or invalidation age reached
            return False

        # Consider locked if pid stated in lock file is still valid
        pid = runez.to_int(runez.first_line(self.lock))
        return runez.check_pid(pid)

    def _should_keep(self):
        """Should we keep folder after lock release?"""
        return runez.is_younger(self.folder, self.keep)

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
        runez.write(self.lock, "%s\n" % os.getpid())

        if not self._should_keep():
            runez.delete(self.folder, logger=LOG.debug if self.keep else None)

        return self

    def __exit__(self, *_):
        """
        Release lock
        """
        if not self._should_keep():
            runez.delete(self.folder, logger=LOG.debug if self.keep else None)
        runez.delete(self.lock, logger=None)


def vrun(package_spec, command, *args, **kwargs):
    """
    Run command + args from an on-the-fly create virtualenv, for associated pypi 'package_spec'.
    This allows us to run commands like 'pex ...' with pex installed when/if needed

    :param system.PackageSpec package_spec: Associated pypi package the run is for
    :param str command: Command to run (pip, pex, etc...)
    :param args: Command line args
    :param kwargs: Optional named args to pass-through to runez.run_program()
    """
    python = system.target_python(package_spec=package_spec)
    folder = system.SETTINGS.meta.full_path(".%s" % python.short_name)
    with SoftLock(folder, timeout=system.SETTINGS.install_timeout, invalid=system.SETTINGS.install_timeout, keep=10) as lock:
        shared = SharedVenv(lock, python)
        return shared._run_from_venv(command, *args, **kwargs)


def virtualenv_path():
    """
    :return str: Path to our own virtualenv.py
    """
    import virtualenv
    path = virtualenv.__file__
    if path and path.endswith(".pyc"):
        path = path[:-1]
    return path


class SharedVenv(object):
    def __init__(self, lock, venv_python):
        """
        :param SoftLock lock: Acquired lock
        """
        self.venv_python = venv_python
        self.lock = lock
        self.folder = lock.folder
        self.bin = os.path.join(self.folder, "bin")
        self.python = os.path.join(self.bin, "python")
        self.pip = os.path.join(self.bin, "pip")
        self._frozen = None
        if runez.is_younger(self.python, self.lock.keep):
            return
        runez.delete(self.folder)
        venv = virtualenv_path()
        if not venv:
            runez.abort("Can't determine path to virtualenv.py")

        runez.run(self.venv_python.executable, venv, self.folder)

    @property
    def frozen_path(self):
        return os.path.join(self.folder, "frozen.json")

    @property
    def frozen(self):
        if self._frozen is None:
            self._frozen = runez.read_json(self.frozen_path, default={})
        return self._frozen or {}

    def _run_pip(self, *args, **kwargs):
        args = runez.flattened(args, split=runez.SHELL)
        return runez.run(self.pip, *args, **kwargs)

    def _refresh_frozen(self):
        output = self._run_pip("freeze", "--all", fatal=False)
        self._frozen = {}
        if output:
            for line in output.split("\n"):
                name, version = system.despecced(line)
                self._frozen[name] = version
        if self._frozen:
            runez.save_json(self._frozen, self.frozen_path)

    def _installed_module(self, command_spec):
        """
        :param system.PackageSpec command_spec: Associated package spec
        :param str command: Program to install in venv, if not already installed
        """
        program = os.path.join(self.bin, command_spec.dashed)
        current = self.frozen.get(command_spec.dashed)
        if not current and runez.is_executable(program):
            # Edge case for older versions that weren't based on freeze
            self._refresh_frozen()
            current = self.frozen.get(command_spec.dashed)
        if not current or (command_spec.version and current != command_spec.version):
            self._run_pip("install", "-i", system.SETTINGS.index, command_spec.specced)
            self._refresh_frozen()
        return program

    def _run_from_venv(self, command, *args, **kwargs):
        """
        Should be called while holding the soft file lock in context only

        :param str command: Command to run from that package (optionally specced with version)
        :param args: Args to invoke program with
        :param kwargs: Additional args
        """
        cmd = system.PackageSpec(command)
        if cmd.dashed == "pip":
            return self._run_pip(*args, **kwargs)
        args = runez.flattened(args, split=runez.SHELL)
        full_path = self._installed_module(cmd)
        return runez.run(full_path, *args, **kwargs)
