import os
import time

from pickley import system


class PingLockException(Exception):
    """Raised when ping lock can't be acquired"""

    def __init__(self, ping_path):
        self.ping_path = ping_path


class PingLock:
    """
    Allows to manage .work/ folder with a .ping lock file
    Several pickley processes could be attempting to auto upgrade a package at the same time
    With this class, we make it so:
    - first process "grabs a lock" via a .ping file (lock based on existence of file, and its age)
    - lock consists of creating a .work/.ping file, and deleting .work/ folder once installation completes
    - other processes will avoid trying their own upgrade during that time
    - the lock remains valid for an hour, after that previous upgrade attempt is considered failed (lock re-acquired)
    """

    def __init__(self, folder, seconds):
        """
        :param str folder: Target installation folder (<base>/.pickley/<name>/.work)
        :param float seconds: Number of seconds ping file is valid for (default: 1 hour)
        """
        self.folder = folder
        self.seconds = seconds
        self.ping = os.path.join(self.folder, ".ping")

    def __enter__(self):
        """
        Grab a folder/.ping lock if possible, raise PingLockException if not
        """
        if system.file_younger(self.ping, self.seconds):
            raise PingLockException(self.ping)
        system.delete_file(self.folder)
        system.touch(self.ping)
        return self

    def __exit__(self, *_):
        """
        Delete folder (with its .ping file)
        """
        system.delete_file(self.folder)


class SoftLockException(Exception):
    """Raised when soft lock can't be acquired"""

    def __init__(self, folder):
        self.folder = folder


class SoftLock:
    """
    Simple soft file lock mechanism
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

        return self

    def __exit__(self, *_):
        """
        Release lock
        """
        if not self._should_keep():
            system.delete_file(self.folder)
        system.delete_file(self.lock)
