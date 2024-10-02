import logging
import os

import runez

from pickley import abort, bstrap, CFG

LOG = logging.getLogger(__name__)

WRAPPER_MARK = "# Wrapper generated by https://pypi.org/project/pickley/"

GENERIC_WRAPPER = (
    """
#!/bin/bash

# pypi-package: {name}
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
"""
    % WRAPPER_MARK
)

# Specific wrapper for pickley itself (avoid calling ourselves back recursively for auto-upgrade)
PICKLEY_WRAPPER = (
    """
#!/bin/bash

# pypi-package: pickley
%s

if [[ -x {source} ]]; then
    if [[ "$*" != *"auto-"* ]]; then
        {hook}nohup {source} auto-upgrade {name}{bg}
    fi
    {hook}exec {source} "$@"
else
    echo "{source} is not available anymore"
    echo ""
    echo "Please reinstall with:"
    echo '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/codrsquad/pickley/main/get-pickley)"'
    exit 1
fi
"""
    % WRAPPER_MARK
)


class DeliveryMethod:
    """
    Various implementation of delivering the actual executables
    """

    action = "Delivered"
    short_name = "deliver"

    @classmethod
    def delivery_method_by_name(cls, name):
        """
        Args:
            name (str): Name of delivery method

        Returns:
            (DeliveryMethod): Associated delivery method
        """
        if name == "wrap":
            return DeliveryMethodWrap()

        if name == "symlink":
            return DeliveryMethodSymlink()

        return abort(f"Unknown delivery method '{runez.red(name)}'")

    def install(self, pspec):
        """
        Args:
            pspec (pickley.PackageSpec): Package spec being installed
        """
        try:
            prev_manifest = pspec.manifest
            for name in pspec.resolved_info.entrypoints:
                src = os.path.join(pspec.target_installation_folder, "bin", name)
                dest = pspec.exe_path(name)
                short_src = runez.short(src)
                short_dest = runez.short(dest)
                if runez.DRYRUN:
                    print(f"Would {self.short_name} {short_dest} -> {short_src}")
                    continue

                if not os.path.exists(src):
                    abort(f"Can't {self.short_name} {short_dest} -> {runez.red(short_src)}: source does not exist")

                LOG.debug("%s %s -> %s", self.action, short_dest, short_src)
                self._install(pspec, dest, src)

            manifest = pspec.save_manifest()
            if not runez.DRYRUN and prev_manifest and prev_manifest.entrypoints:
                for old_ep in prev_manifest.entrypoints:
                    if old_ep and old_ep not in manifest.entrypoints:
                        # Remove old entry points that are not in new manifest anymore
                        runez.delete(pspec.exe_path(old_ep))

            return manifest

        except Exception as e:
            abort(f"Failed to {self.short_name} {pspec}: {runez.red(e)}")

    def _install(self, pspec, target, source):
        raise NotImplementedError(f"{self.__class__.__name__} is not implemented")


class DeliveryMethodSymlink(DeliveryMethod):
    """
    Deliver via symlink
    """

    action = "Symlinked"
    short_name = "symlink"

    def _install(self, pspec, target, source):
        runez.delete(target, logger=False)
        if os.path.isabs(source) and os.path.isabs(target):
            parent = runez.parent_folder(target)
            if runez.parent_folder(source).startswith(parent):
                # Use relative path if source is under target
                source = os.path.relpath(source, parent)

        os.symlink(source, target)


class DeliveryMethodWrap(DeliveryMethod):
    """
    Deliver via a small wrap that ensures target executable is up-to-date
    """

    action = "Wrapped"
    short_name = "wrap"

    # Can be set in tests to make wrapper a no-op
    hook = ""
    bg = " &> /dev/null &"

    def _install(self, pspec, target, source):
        pickley = CFG.base.full_path(bstrap.PICKLEY)
        wrapper = PICKLEY_WRAPPER if pspec.canonical_name == bstrap.PICKLEY else GENERIC_WRAPPER
        contents = wrapper.lstrip().format(
            hook=self.hook,
            bg=self.bg,
            name=runez.quoted(pspec.canonical_name, adapter=None),
            pickley=runez.quoted(pickley, adapter=None),
            source=runez.quoted(source, adapter=None),
        )
        runez.delete(target, logger=False)
        runez.write(target, contents, logger=False)
        runez.make_executable(target, logger=False)
