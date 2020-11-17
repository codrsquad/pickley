import os
import re
import sys

import runez


RE_PYTHON_LOOSE_VERSION = re.compile(r"(py(thon *)?)?([0-9]+)?\.?([0-9]+)?\.?([0-9]*)", re.IGNORECASE)


def py_version_components(text, loose=True):
    m = RE_PYTHON_LOOSE_VERSION.match(text)
    if m and m.group(0) == text:
        components = [s for s in (m.group(3), m.group(4), m.group(5)) if s]
        if loose and len(components) == 1:
            # Support notation of the form: py37
            return [c for c in components[0]]

        return components


def std_python_name(desired):
    """
    >>> std_python_name("py37")
    'python3.7'
    >>> std_python_name("3")
    'python3'

    Args:
        desired (str | None): 'desired' python version as given by CLI flag or configuration

    Returns:
        (str | None): Short notations such as "37" or "py37" expanded to "python3.7"
    """
    if not desired:
        return "python"

    components = py_version_components(desired)
    if components is not None and len(components) <= 3:
        return "python%s" % ".".join(components)

    return desired


class PythonInstallation(object):
    """Describes a specific python installation"""

    executable = None  # type: str # Full path to python executable
    major = None  # type: int # Major version
    minor = None  # type: int # Minor version
    patch = None  # type: int # Patch revision
    problem = None  # type: str # String describing a problem with this installation, if there is one

    def __repr__(self):
        return runez.short(self.executable)

    def __eq__(self, other):
        if not isinstance(other, PythonInstallation):
            return False

        if self.executable:
            return self.executable == other.executable

        return not other.executable and (self.version, self.problem) == (other.version, other.problem)

    def __ne__(self, other):
        return not (self == other)

    @property
    def version(self):
        return ".".join(str(c) for c in (self.major, self.minor, self.patch) if c is not None)

    @property
    def is_invoker(self):
        return False

    def satisfies(self, desired):
        """
        Args:
            desired (str | None): Canonical 'desired' python version as expressed by user or configuration

        Returns:
            (bool): True if this python installation satisfies it
        """
        if desired == self.executable:
            return True

        if not desired or self.problem:
            return False

        this = std_python_name(self.version)
        if this.startswith(desired):
            return True


def python_exe_path(venv, major=None):
    if venv:
        if major:
            names = ("python%s" % major, "python")

        else:
            names = ("python", "python3", "python2")

        for name in names:
            path = os.path.join(venv, "bin", name)
            if runez.DRYRUN or runez.is_executable(path):
                return path


class InvokerPython(PythonInstallation):
    """Python currently running pickley"""

    def __init__(self):
        v = sys.version_info
        self.major = v[0]
        self.minor = v[1]
        self.patch = v[2]
        prefix = getattr(sys, "real_prefix", None)
        if not prefix:
            prefix = getattr(sys, "base_prefix", sys.prefix)

        self.executable = self.simplified_python_executable(prefix, self.major)

    @property
    def is_invoker(self):
        return True

    @staticmethod
    def simplified_python_executable(prefix, major):
        """Path to python that created pickley's venv"""
        if prefix:
            if "Python3.framework" in prefix and "Versions/3" in prefix:  # Simplify OSX ridiculous paths
                return "/usr/bin/python3"

            elif "Python.framework" in prefix and "Versions/2" in prefix:
                return "/usr/bin/python"

            path = python_exe_path(prefix, major=major)
            if path:
                return path

        return sys.executable  # Running from pex (NOT a venv)


class PythonFromPath(PythonInstallation):
    """Python installation from a specific local path"""

    def __init__(self, path, version=None):
        """
        Args:
            path (str): Path to a python executable
        """
        self.executable = path
        if not runez.is_executable(path):
            self.problem = "not an executable"
            return

        if not version:
            r = runez.run(self.executable, "--version", dryrun=False, fatal=False, logger=None)
            if not r.succeeded:
                self.problem = "does not respond to --version: %s" % runez.short(r.full_output)
                return

            version = r.full_output

        m = RE_PYTHON_LOOSE_VERSION.search(version)
        if m:
            self.major = runez.to_int(m.group(3))
            self.minor = runez.to_int(m.group(4))
            self.patch = runez.to_int(m.group(5))

        if not self.major:
            self.problem = "--version did not yield major version component"


class UnknownPython(PythonInstallation):
    """Holds a problematic reference to an unknown python"""

    def __init__(self, desired):
        self.executable = desired
        self.problem = "not available"


class AvailablePythons(object):
    """Formalizes how to run external pythons, respecting desired python as specified via configuration or CLI"""

    def __init__(self, scanner=None):
        self.scanner = scanner
        self.invoker = InvokerPython()
        self._available = []
        self._cache = None
        self._scanner = None

    def find_python(self, desired=runez.UNSET):
        """
        Args:
            desired (str | PythonInstallation | None): Desired python

        Returns:
            (PythonInstallation): Object representing python installation (may not be usable, see reported .problem)
        """
        if isinstance(desired, PythonInstallation):
            return desired

        if isinstance(desired, runez.system.string_type):
            desired = desired.strip()

        if not desired or not isinstance(desired, runez.system.string_type):
            # Don't bother scanning/caching anything until a specifically desired python is needed
            # Non-string is for backwards compatibility, older configs may have had a dict
            return self.invoker

        return self._find_python(desired)

    def _find_python(self, desired):
        """
        Args:
            desired (str): Desired python

        Returns:
            (PythonInstallation): Determined python installation
        """
        if self._cache is None:
            # Seed cache with invoker, this will satisfy some 'desired' lookups
            self._cache = {}
            self._register(self.invoker)
            self._register_python_install(self.invoker, "python")
            self._register_python_install(self.invoker, "invoker")

        python = self._cache.get(desired)
        if python:
            return python

        if os.path.isabs(desired):
            # Absolute path: look it up and remember it
            python = PythonFromPath(desired)
            self._register(python)
            return python

        std_name = std_python_name(desired)
        python = self._cache.get(std_name)
        if python:
            return python

        if self._scanner is None:
            # We need to try harder: do a lazy scan (scan only as little as necessary)
            self._scanner = iter(self._scan())

        while self._scanner != StopIteration:
            try:
                python = next(self._scanner)
                self._register(python)
                if python.satisfies(std_name):
                    return python

            except StopIteration:
                self._scanner = StopIteration

        python = UnknownPython(desired)
        self._register(python)
        return python

    def _register_python_install(self, python, name):
        if name and name not in self._cache:
            self._cache[name] = python

    def _register(self, python):
        self._register_python_install(python, python.executable)
        if not python.problem:
            self._register_python_install(python, "python%s" % python.major)
            self._register_python_install(python, "python%s.%s" % (python.major, python.minor))
            self._register_python_install(python, "python%s" % python.version)
            self._available.append(python)

    def _scan(self):
        if self.scanner:
            for python in self.scanner():
                yield python
