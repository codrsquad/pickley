import runez

from pickley import system


class ImplementationMap(object):
    """
    Keep track of implementations by name, configurable via settings
    """

    def __init__(self, key):
        """
        :param str key: Key identifying this implementation map
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
        implementation.implementation_name = name
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

    def resolved_name(self, package_spec, default=None):
        """
        :param system.PackageSpec package_spec: Pypi package spec
        :param default: Optional default value (takes precedence over system.SETTINGS.defaults only)
        :return str: Corresponding implementation name to use
        """
        definition = system.SETTINGS.resolved_definition(self.key, package_spec=package_spec, default=default)
        if not definition or not definition.value:
            return None

        return definition.value

    def resolved(self, package_spec, default=None):
        """
        :param system.PackageSpec package_spec: Pypi package spec
        :param default: Optional default value (takes precedence over system.SETTINGS.defaults only)
        :return: Corresponding implementation to use
        """
        name = self.resolved_name(package_spec, default=default)
        name, version = system.despecced(name)
        if not name:
            runez.abort("No %s type configured for %s", self.key, package_spec)

        implementation = self.get(name)
        if not implementation:
            runez.abort("Unknown %s type '%s'", self.key, name)

        imp = implementation(package_spec)
        imp.implementation_version = version
        return imp
