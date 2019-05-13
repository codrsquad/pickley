import logging
import os

import runez

from pickley import system
from pickley.context import ImplementationMap
from pickley.settings import short

LOG = logging.getLogger(__name__)
DELIVERERS = ImplementationMap("delivery")

GENERIC_WRAPPER = """
#!/bin/bash

%s

if [[ -x {pickley} ]]; then
    {hook}nohup {pickley} auto-upgrade {name}{bg}
fi
if [[ -x {source} ]]; then
    {hook}exec {source} "$@"
else
    echo "{source} is not available anymore"
    echo ""
    echo "Please reinstall with:"
    echo "{pickley} install -f {name}"
    exit 1
fi
""" % system.WRAPPER_MARK

# Specific wrapper for pickley itself (avoid calling ourselves back recursively for auto-upgrade)
PICKLEY_WRAPPER = """
#!/bin/bash

%s

if [[ -x {source} ]]; then
    if [[ "$*" != *"auto-upgrade"* ]]; then
        {hook}nohup {source} auto-upgrade {name}{bg}
    fi
    {hook}exec {source} "$@"
else
    echo "{source} is not available anymore"
    echo ""
    echo "Please reinstall with:"
    url=`curl -s https://pypi.org/pypi/pickley/json | grep -Eo '"download_url":"([^"]+)"' | cut -d'"' -f4`
    echo curl -sLo {pickley} $url
    exit 1
fi
""" % system.WRAPPER_MARK


class DeliveryMethod(object):
    """
    Various implementation of delivering the actual executables
    """

    implementation_name = None  # type: str # Injected by ImplementationMap

    def __init__(self, package_spec):
        """
        Args:
            package_spec (system.PackageSpec): Associated pypi package spec
        """
        self.package_spec = package_spec

    def install(self, target, source):
        """
        :param str target: Full path of executable to deliver (<base>/<entry_point>)
        :param str source: Path to original executable being delivered (.pickley/<package>/...)
        """
        runez.delete(target, logger=None)
        if runez.DRYRUN:
            LOG.debug("Would %s %s (source: %s)", self.implementation_name, short(target), short(source))
            return

        if not os.path.exists(source):
            runez.abort("Can't %s, source %s does not exist", self.implementation_name, short(source))

        try:
            LOG.debug("Delivering %s %s -> %s", self.implementation_name, short(target), short(source))
            self._install(target, source)

        except Exception as e:
            runez.abort("Failed %s %s: %s", self.implementation_name, short(target), e)

    def _install(self, target, source):
        """
        :param str target: Full path of executable to deliver (<base>/<entry_point>)
        :param str source: Path to original executable being delivered (.pickley/<package>/...)
        """


@DELIVERERS.register
class DeliveryMethodSymlink(DeliveryMethod):
    """
    Deliver via symlink
    """

    def _install(self, target, source):
        if os.path.isabs(source) and os.path.isabs(target):
            parent = runez.parent_folder(target)
            if runez.parent_folder(source).startswith(parent):
                # Use relative path if source is under target
                source = os.path.relpath(source, parent)
        os.symlink(source, target)


@DELIVERERS.register
class DeliveryMethodWrap(DeliveryMethod):
    """
    Deliver via a small wrap that ensures target executable is up-to-date
    """

    # Can be set in tests to make wrapper a no-op
    hook = ""
    bg = " &> /dev/null &"

    def _install(self, target, source):
        wrapper = PICKLEY_WRAPPER if self.package_spec.dashed == system.PICKLEY else GENERIC_WRAPPER
        contents = wrapper.lstrip().format(
            hook=self.hook,
            bg=self.bg,
            name=runez.quoted(self.package_spec.dashed),
            pickley=runez.quoted(system.SETTINGS.base.full_path(system.PICKLEY)),
            source=runez.quoted(source),
        )
        runez.write(target, contents)
        runez.make_executable(target)


@DELIVERERS.register
class DeliveryMethodCopy(DeliveryMethod):
    """
    Deliver by copy
    """

    def _install(self, target, source):
        copy_venv(source, target)


def copy_venv(source, destination, fatal=True, logger=LOG.debug):
    """
    Copy source -> destination

    :param str source: Source file or folder
    :param str destination: Destination file or folder
    :param bool fatal: Abort execution on failure if True
    :param callable|None logger: Logger to use
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    return runez.copy(source, destination, adapter=_relocator, fatal=fatal, logger=logger)


def move_venv(source, destination, fatal=True, logger=LOG.debug):
    """
    Move source -> destination

    :param str source: Source file or folder
    :param str destination: Destination file or folder
    :param bool fatal: Abort execution on failure if True
    :param callable|None logger: Logger to use
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    return runez.move(source, destination, adapter=_relocator, fatal=fatal, logger=logger)


def _relocator(source, destination, fatal=True, logger=None):
    """Adapter for move/copy file"""
    relocated = relocate_venv(source, source, destination, fatal=fatal, logger=logger)
    return " (relocated %s)" % relocated if relocated else ""


def relocate_venv(path, source, destination, fatal=True, logger=LOG.debug, _seen=None):
    """
    :param str path: Path of file or folder to relocate (change mentions of 'source' to 'destination')
    :param str source: Where venv used to be
    :param str destination: Where venv is moved to
    :param bool fatal: Abort execution on failure if True
    :param callable|None logger: Logger to use
    :return int: Number of relocated files (0 if no-op, -1 on failure)
    """
    original_call = False
    if _seen is None:
        original_call = True
        _seen = set()

    if not path or path in _seen:
        return 0

    _seen.add(path)
    if os.path.isdir(path):
        relocated = 0
        if original_call:
            for bin_folder in find_venvs(path):
                for name in os.listdir(bin_folder):
                    fpath = os.path.join(bin_folder, name)
                    r = relocate_venv(fpath, source, destination, fatal=fatal, logger=logger, _seen=_seen)
                    if r < 0:
                        return r
                    relocated += r
            if logger and relocated:
                logger("Relocated %s files in %s: %s -> %s", relocated, short(path), short(source), short(destination))
        return relocated

    content = runez.get_lines(path, fatal=fatal)
    if not content:
        return 0

    modified = False
    lines = []
    for line in content:
        if source in line:
            line = line.replace(source, destination)
            modified = True
        lines.append(line)

    if not modified:
        return 0

    r = runez.write(path, "".join(lines), fatal=fatal)
    if r > 0 and logger and original_call:
        logger("Relocated %s: %s -> %s", short(path), short(source), short(destination))
    return r


def find_venvs(folder, _seen=None):
    """
    :param str folder: Folder to scan for venvs
    :param set|None _seen: Allows to not get stuck on circular symlinks
    """
    if folder and os.path.isdir(folder):
        if _seen is None:
            folder = os.path.realpath(folder)
            _seen = set()
        if folder not in _seen:
            _seen.add(folder)
            files = os.listdir(folder)
            if "bin" in files:
                bin_folder = os.path.join(folder, "bin")
                if runez.is_executable(os.path.join(bin_folder, "python")):
                    yield bin_folder
                    return
            for name in files:
                fname = os.path.join(folder, name)
                for path in find_venvs(fname, _seen=_seen):
                    yield path
