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

    def is_young(self, seconds=None):
        """
        :param float|None seconds: Number of seconds .ping is considered young (default: self.seconds)
        :return bool: True if .ping file exists, and is younger than 'seconds'
        """
        if not os.path.exists(self.ping):
            return False
        mtime = os.path.getmtime(self.ping)
        if seconds is None:
            seconds = self.seconds
        cutoff = time.time() - seconds
        return mtime >= cutoff

    def touch(self):
        """Touch the .ping file"""
        system.write_contents(self.ping, "")

    def __enter__(self):
        """
        Grab a folder/.ping lock if possible, raise PingLockException if not
        """
        if self.is_young():
            raise PingLockException(self.ping)
        system.delete_file(self.folder)
        self.touch()
        return self

    def __exit__(self, *_):
        """
        Delete folder (with its .ping file)
        """
        system.delete_file(self.folder)
