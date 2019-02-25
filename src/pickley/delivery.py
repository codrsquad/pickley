import logging
import os

import runez

from pickley import system
from pickley.context import ImplementationMap
from pickley.system import short

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

    def __init__(self, package_name):
        self.package_name = package_name

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
        wrapper = PICKLEY_WRAPPER if self.package_name == system.PICKLEY else GENERIC_WRAPPER
        contents = wrapper.lstrip().format(
            hook=self.hook,
            bg=self.bg,
            name=runez.quoted(self.package_name),
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
        system.copy(source, target)
