import logging
import os
import sys

from six import StringIO

from pickley import decode, system


class ImplementationMap:
    """
    Keep track of implementations by name, configurable via settings
    """

    def __init__(self, key):
        """
        :param str key: Key in setting where to lookup default to use
        """
        self.key = key
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

    def resolved_name(self, package_name, default=None):
        """
        :param str package_name: Name of pypi package
        :param default: Optional default value (takes precendence over system.SETTINGS.defaults only)
        :return str: Corresponding implementation name to use
        """
        definition = system.SETTINGS.resolved_definition(self.key, package_name=package_name, default=default)
        if not definition or not definition.value:
            return None

        return definition.value

    def resolved(self, package_name, default=None):
        """
        :param str package_name: Name of pypi package
        :param default: Optional default value (takes precendence over system.SETTINGS.defaults only)
        :return: Corresponding implementation to use
        """
        name = self.resolved_name(package_name, default=default)
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

    def __init__(self, stdout=True, stderr=True, dryrun=None):
        """
        :param bool stdout: Capture stdout
        :param bool stderr: Capture stderr
        :param bool|None dryrun: Override dryrun (when provided)
        """
        self.dryrun = dryrun
        self.old_out = sys.stdout
        self.old_err = sys.stderr
        self.old_handlers = logging.root.handlers

        self.out_buffer = StringIO() if stdout else self.old_out
        self.err_buffer = StringIO() if stderr else self.old_err

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

        if self.dryrun is not None:
            system.DRYRUN = self.dryrun

    def __contains__(self, item):
        return item is not None and item in str(self)
