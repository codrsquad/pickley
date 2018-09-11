import os

from pickley import short, system
from pickley.context import ImplementationMap
from pickley.lock import PingLock
from pickley.settings import SETTINGS

DELIVERERS = ImplementationMap(SETTINGS, "delivery")

GENERIC_WRAPPER = """
#!/bin/bash

%s

if [[ -x {pickley} ]]; then
    nohup {pickley} auto-upgrade {name} &> /dev/null &
fi
if [[ -x {source} ]]; then
    exec {source} "$@"
else
    echo "{source} is not available anymore"
    echo ""
    echo "Please reinstall with:"
    echo "{pickley} install -f {name}"
    exit 1
fi
""" % system.WRAPPER_MARK

# Specific wrapper for pickley itself (better handling bootstrap)
PICKLEY_WRAPPER = """
#!/bin/bash

%s

if [[ -x {source} ]]; then
    if [[ $1 != "auto-upgrade" ]]; then
        nohup {source} auto-upgrade {name} &> /dev/null &
    fi
    exec {source} "$@"
else
    echo "{source} is not available anymore"
    echo ""
    echo "Please reinstall with:"
    url=`curl -s https://pypi.org/pypi/pickley/json | grep -Eo '"download_url":"([^"]+)"' | cut -d'"' -f4`
    echo curl -sLo {pickley} $url
    exit 1
fi
""" % system.WRAPPER_MARK


class DeliveryMethod:
    """
    Various implementation of delivering the actual executables
    """

    registered_name = None  # type: str # Injected by ImplementationMap

    def __init__(self, package_name):
        self.package_name = package_name

    def install(self, target, source):
        """
        :param str target: Full path of executable to deliver (<base>/<entry_point>)
        :param str source: Path to original executable being delivered (.pickley/<package>/...)
        """
        system.delete_file(target)
        if system.DRYRUN:
            system.debug("Would %s %s (source: %s)", self.registered_name, short(target), short(source))
            return

        if not os.path.exists(source):
            system.abort("Can't %s, source %s does not exist", self.registered_name, short(source))

        try:
            system.debug("Delivery: %s %s -> %s", self.registered_name, short(target), short(source))
            self._install(target, source)

        except Exception as e:
            system.abort("Failed %s %s: %s", self.registered_name, short(target), e)

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
            parent = system.parent_folder(target)
            if system.parent_folder(source).startswith(parent):
                # Use relative path if source is under target
                source = os.path.relpath(source, parent)
        os.symlink(source, target)


@DELIVERERS.register
class DeliveryMethodWrap(DeliveryMethod):
    """
    Deliver via a small wrap that ensures target executable is up-to-date
    """

    def _install(self, target, source):
        # Touch the .ping file since this is a fresh install (no need to check for upgrades right away)
        ping = PingLock(SETTINGS.meta.full_path(self.package_name), seconds=SETTINGS.version_check_delay)
        ping.touch()

        if self.package_name == system.PICKLEY:
            # Important: call pickley auto-upgrade from souce, and not wrapper in order to avoid infinite recursion
            wrapper = PICKLEY_WRAPPER
        else:
            wrapper = GENERIC_WRAPPER

        contents = wrapper.lstrip().format(
            name=system.quoted(self.package_name),
            pickley=system.quoted(SETTINGS.base.full_path(system.PICKLEY)),
            source=system.quoted(source),
        )
        system.write_contents(target, contents)
        system.make_executable(target)


@DELIVERERS.register
class DeliveryMethodCopy(DeliveryMethod):
    """
    Deliver by copy
    """

    def _install(self, target, source):
        system.copy_file(source, target)
