import logging
import os
import sys

import six

from pickley import decode, system


class ImplementationMap:
    """
    Keep track of implementations by name, configurable via settings
    """

    def __init__(self, settings, key):
        """
        :param pickley.settings.Settings: Settings to use
        :param str key: Key in setting where to lookup default to use
        """
        self.key = key
        self.settings = settings
        self.map = {}

    def register(self, implementation):
        """
        :param type implementation: Class to register
        """
        name = implementation.__name__
        for parent in implementation.__bases__:
            parent = parent.__name__
            if name.startswith(parent):
                name = name[len(parent):]
            elif name.endswith(parent):
                name = name[:-len(parent)]
        name = name.lower()
        implementation.registered_name = name
        self.map[name] = implementation
        return implementation

    def get(self, name):
        """
        :param str name: Name of implementation
        :return: Registered implementation, if any
        """
        return self.map.get(name and name.lower())

    def names(self):
        """
        :return list(str): Registered names
        """
        return sorted(self.map.keys())

    def resolved_name(self, package_name):
        """
        :param str package_name: Name of pypi package
        :return str: Corresponding implementation name to use
        """
        definition = self.settings.resolved_definition(self.key, package_name=package_name)
        if not definition or not definition.value:
            return None

        return definition.value

    def resolved(self, package_name):
        """
        :param str package_name: Name of pypi package
        :return: Corresponding implementation to use
        """
        name = self.resolved_name(package_name)
        if not name:
            system.abort("No %s type configured for %s", self.key, package_name)

        implementation = self.get(name)
        if not implementation:
            system.abort("Unknown %s type '%s'", self.key, name)

        return implementation(package_name)


class CurrentFolder:
    """Context manager for changing the current working directory"""

    def __init__(self, destination):
        self.destination = system.resolved_path(destination)

    def __enter__(self):
        self.current_folder = os.getcwd()
        os.chdir(self.destination)

    def __exit__(self, *_):
        os.chdir(self.current_folder)


class CaptureOutput:
    """
    Context manager allowing to temporarily grab stdout/stderr output.
    Output is captured and made available only for the duration of the context.

    Sample usage:

    with CaptureOutput() as logged:
        ... do something that generates output ...
        assert "some message" in logged
    """

    def __init__(self, folder=None, stdout=True, stderr=True, env=None, dryrun=None):
        """
        :param str|None folder: Change cwd to 'folder' when provided
        :param bool stdout: Capture stdout
        :param bool stderr: Capture stderr
        :param dict|None env: Customize PATH-like env vars when provided
        :param bool|None dryrun: Switch dryrun when provided
        """
        self.current_folder = os.getcwd()
        self.folder = folder
        self.env = env
        self.dryrun = dryrun
        self.old_env = {}
        self.old_out = sys.stdout
        self.old_err = sys.stderr
        self.old_handlers = logging.root.handlers

        self.out_buffer = six.StringIO() if stdout else self.old_out
        self.err_buffer = six.StringIO() if stderr else self.old_err

        self.handler = logging.StreamHandler(stream=self.err_buffer)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))

    def __repr__(self):
        result = ""
        if self.out_buffer:
            result += decode(self.out_buffer.getvalue())
        if self.err_buffer:
            result += decode(self.err_buffer.getvalue())
        return result

    def __enter__(self):
        if self.folder:
            system.ensure_folder(self.folder, folder=True)

        self.old_env = {}
        for key, value in os.environ.items():
            self.old_env[key] = os.environ.get(key)

        if self.env:
            for key, value in self.env.items():
                if value:
                    if value != os.environ.get(key):
                        system.debug("Customizing env %s=%s", key, value)
                        os.environ[key] = value
                elif key in os.environ:
                    system.debug("Removing env %s", key)
                    del os.environ[key]

        sys.stdout = self.out_buffer
        sys.stderr = self.err_buffer
        logging.root.handlers = [self.handler]

        if self.dryrun is not None:
            (system.DRYRUN, self.dryrun) = (bool(self.dryrun), bool(system.DRYRUN))

        return self

    def __exit__(self, *args):
        sys.stdout = self.old_out
        sys.stderr = self.old_err
        self.out_buffer = None
        self.err_buffer = None
        logging.root.handlers = self.old_handlers

        for key in list(os.environ.keys()):
            if key not in self.old_env:
                system.debug("Cleaning up env %s", key)
                del os.environ[key]

        for key, value in self.old_env.items():
            if value != os.environ.get(key):
                system.debug("Restoring env %s=%s", key, value)
                os.environ[key] = value

        if self.dryrun is not None:
            system.DRYRUN = self.dryrun

    def __contains__(self, item):
        return item is not None and item in str(self)
