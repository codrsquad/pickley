# coding=utf-8
"""
Simple json configuration system

<base>: installation folder, example ~/.local/bin

tree <base>
├── .pickley/                       # Folder where pickley will build/manage/track installations
│   ├── audit.log                   # Activity is logged here
│   ├── config.json                 # Optional configuration provided by user
│   ├── tox/
│   │   ├── .current.json           # Currently installed version
│   │   ├── .latest.json            # Latest version as determined by querying pypi
│   │   ├── .tmp/                   # Temp folder used during installation
│   │   ├── .tmp.lock               # Soft lock file containing pid of pickley process that currently hold the lock on .tmp/
│   │   └── tox-2.9.1/              # Actual installation, as packaged by pickley
├── pickley                         # pickley itself
└── tox -> .pickley/tox/2.9.1/...   # Produced exe, can be a symlink or a small wrapper exe (to ensure up-to-date)
"""

import logging
import os

import runez

from pickley import system


LOG = logging.getLogger(__name__)
DOT_PICKLEY = ".pickley"
DEFAULT_INSTALL_TIMEOUT = 30
DEFAULT_VERSION_CHECK_DELAY = 10
REPRESENTATION_WIDTH = 90


def short(path, meta=True):
    """
    :param path: Path to represent in its short form
    :param bool meta: If True, shorten paths relatively to SYSTEM.meta as well
    :return str: Short form, using '~' if applicable
    """
    if not path:
        return path
    if not meta:
        runez.Anchored.pop(system.SETTINGS.meta.path)
    result = runez.short(str(path))
    if not meta:
        runez.Anchored.add(system.SETTINGS.meta.path)
    return result


class FolderBase(object):
    """
    This class allows to more easily deal with folders
    """

    def __init__(self, path, name=None):
        """
        :param str path: Path to folder
        :param str|None name: Name of this folder (defaults to basename of 'path')
        """
        self.path = runez.resolved_path(path)
        self.name = name or os.path.basename(path)

    def relative_path(self, path):
        """
        :param str path: Path to relativize
        :return str: 'path' relative to self.path
        """
        return os.path.relpath(path, self.path)

    def relativize(self, component):
        return component or "" if not component or not component.startswith("/") else component[1:]

    def full_path(self, *relative):
        """
        :param list(str) *relative: Relative components
        :return str: Full path based on self.path
        """
        relative = [self.relativize(c) for c in relative]
        return os.path.join(self.path, *relative)

    def __repr__(self):
        return "%s: %s" % (self.name, short(self.path))


def meta_folder(path):
    """
    :param str path: Path to folder to use
    :return FolderBase: Associated object
    """
    return FolderBase(os.path.join(path, DOT_PICKLEY), name="meta")


def add_representation(result, data, indent=""):
    """
    :param list result: Where to add lines representing 'data'
    :param dict|list|str data: Data to represent
    :param str indent: Indentation to use
    """
    if not data:
        return
    if isinstance(data, list):
        for item in data:
            result.append("%s- %s" % (indent, short(item)))
        return
    if isinstance(data, dict):
        for key, value in sorted(data.items()):
            if isinstance(value, list):
                brief = runez.represented_args(value, separator=", ")
                if len(brief) < REPRESENTATION_WIDTH:
                    result.append("%s%s: [%s]" % (indent, short(key), brief))
                    continue
            if isinstance(value, (dict, list)):
                result.append("%s%s:" % (indent, short(key)))
                add_representation(result, value, indent="  %s" % indent)
            else:
                result.append("%s%s: %s" % (indent, short(key), short(value)))
        return
    result.append("%s- %s" % (indent, short(data)))


class Definition(object):
    """
    Defined value, with origin where the value came from
    """

    def __init__(self, value, source, key):
        """
        :param value: Actual value
        :param source: Where value was defined
        :param str key: Under what key it was defined
        """
        self.value = value
        self.source = source
        self.key = key

    def __repr__(self):
        return "%s:%s" % (short(self.source), self.key)


class SettingsFile(object):
    """
    Deserialized json settings file, configures:
    - installation "channel" to use (stable, latest, ...)
    - other setting files to include
    - versions to use per channel
    """

    def __init__(self, parent, path=None, name=None):
        """
        :param Settings parent: Parent settings object
        :param str|None path: Path to settings file
        """
        self.parent = parent
        self.path = path or name
        self.folder = runez.parent_folder(path)
        self._contents = None

    def __repr__(self):
        return short(self.path)

    def set_contents(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, dict):
                kwargs.update(args[0])
        self._contents = kwargs
        self.flatten("bundle", separator=" ")
        self.flatten("include", direct=True)
        bundle = self._contents.get("bundle")
        if isinstance(bundle, dict):
            result = {}
            for name, value in bundle.items():
                result[name] = self.unbundled_names(value)
            self._contents["bundle"] = result

    def unbundled_names(self, names):
        """
        :param list|tuple names: Names to unbundle
        :return set: Resolved full set of names
        """
        result = []
        if names:
            for name in names:
                if name.startswith("bundle:"):
                    bundle = self.get_definition("bundle.%s" % name[7:])
                    if bundle and bundle.value:
                        result.extend(runez.flattened(bundle.value, split=(" ", runez.UNIQUE)))
                        continue
                result.append(name)
        return runez.flattened(result, split=(" ", runez.UNIQUE))

    def flatten(self, key, separator=None, direct=False):
        if not self._contents:
            return
        node = self._contents.get(key)
        if not node:
            return
        if direct:
            self._contents[key] = runez.flattened(node, split=(separator, runez.UNIQUE))
            return
        result = {}
        for name, value in node.items():
            result[name] = runez.flattened(value, split=(separator, runez.UNIQUE))
        self._contents[key] = result

    @property
    def contents(self):
        """
        :return dict: Deserialized contents of settings file
        """
        if self._contents is None:
            self.set_contents(runez.read_json(self.path, default={}, fatal=False))
        return self._contents

    @property
    def include(self):
        """
        :return list(str): Optional list of other settings files to include
        """
        return self.contents.get("include")

    def resolved_definition(self, key, package_spec=None):
        """
        :param str key: Key to look up
        :param system.PackageSpec|None package_spec: Optional associated pypi package spec
        :return Definition|None: Definition corresponding to 'key' in this settings file, if any
        """
        if not key:
            return None
        if package_spec:
            definition = self.get_definition("select.%s.%s" % (package_spec.dashed, key))
            if definition:
                return definition
            main = self.contents.get(key)
            if isinstance(main, dict):
                for name, values in main.items():
                    if isinstance(values, dict):
                        if package_spec.dashed in values:
                            return Definition(name, self, "%s.%s" % (key, name))
                    elif hasattr(values, "split"):
                        if package_spec.dashed in values.split():
                            return Definition(name, self, "%s.%s" % (key, name))
        return self.get_definition("default.%s" % key)

    def get_definition(self, key):
        """
        :param str key: Key to look up
        :return Definition|None: Definition corresponding to 'key' in this settings file, if any
        """
        if not key:
            return None
        if "." in key:
            prefix, _, leaf = key.rpartition(".")
            definition = self.get_definition(prefix)
            if not definition:
                return None
            if isinstance(definition.value, dict):
                value = definition.value.get(leaf)
                if value is not None:
                    return Definition(value, self, key)
                return None
            if definition.value is not None:
                LOG.debug("'%s' is of type %s (not a dict) in '%s'", prefix, type(definition.value), short(self.path))
            return None
        value = self.contents.get(key)
        if value is not None:
            return Definition(value, self, key)
        return None

    def represented(self):
        """
        :return str: Human readable representation of these settings
        """
        if not self.contents:
            return "    - %s: # empty" % short(self.path)
        result = ["    - %s:" % short(self.path)]
        add_representation(result, self.contents, indent="      ")
        return "\n".join(result)


def get_user_index():
    """
    Returns:
        (str | None): User configured pypi index, if any
    """
    conf = runez.get_conf(runez.resolved_path("~/.config/pip/pip.conf"), fatal=None, default={})
    return conf.get("global", {}).get("index-url")


class Settings(object):
    """
    Collection of settings files
    """

    base = None  # type: FolderBase # Installation folder
    meta = None  # type: FolderBase # .pickley meta subfolder

    def __init__(self, base=None):
        """
        :param str|None base: Base folder to use
        """
        self.set_base(base)
        self.cli = SettingsFile(self, name="cli")
        self.defaults = SettingsFile(self, name="defaults")
        self.defaults.set_contents(
            default=dict(
                channel=system.LATEST_CHANNEL,
                delivery=system.DEFAULT_DELIVERY,
                install_timeout=DEFAULT_INSTALL_TIMEOUT,
                packager=system.VENV_PACKAGER,
                version_check_delay=DEFAULT_VERSION_CHECK_DELAY,
            ),
        )
        user_index = get_user_index()
        if user_index:
            self.defaults.contents["index"] = user_index
        self.config = None
        self.config_paths = []
        self.children = []

    def __repr__(self):
        return "[%s] %s" % (len(self.children), self.base)

    def load_config(self, config=None, **cli):
        """
        :param str|None config: Additional configuration file to load
        :param dict cli: Additional entries to consider as top priority (passed via CLI flags)
        """
        self.config = config
        self.config_paths = []
        self.children = []
        self.cli.set_contents(dict((k, v) for k, v in cli.items() if v))
        self._add_config(self.meta.full_path("config.json"))
        if self.config:
            self._add_config(self.config)

    def set_base(self, base):
        """
        :param str|FolderBase|None base: Folder to use as base for installations
        """
        if not base:
            base = os.environ.get("PICKLEY_ROOT")
        if not base:
            base = runez.parent_folder(system.PICKLEY_PROGRAM_PATH)
            if DOT_PICKLEY in base:
                # Don't consider meta folder .pickley/... as installation base
                i = base.index(DOT_PICKLEY)
                base = base[:i].rstrip("/")
            elif ".venv" in base:
                # Convenience for development
                base = runez.parent_folder(base)
                base = os.path.join(base, "root")

        if isinstance(base, FolderBase):
            self.base = base
        else:
            self.base = FolderBase(base, name="base")

        if self.meta:
            runez.Anchored.pop(self.meta.path)
        self.meta = meta_folder(self.base.path)
        runez.Anchored.add(self.meta.path)

    @property
    def install_timeout(self):
        """
        :return float: How many minutes to give an installation to complete before assuming it failed
        """
        return runez.to_int(self.get_value("install_timeout"), default=DEFAULT_INSTALL_TIMEOUT)

    @property
    def version_check_seconds(self):
        """
        :return float: How many seconds to wait before checking for upgrades again
        """
        return runez.to_int(self.get_value("version_check_delay"), default=DEFAULT_VERSION_CHECK_DELAY) * 60

    def _add_config(self, path, base=None):
        """
        :param str path: Path to config file
        :param str|None base: Base path to use to resolve relative paths (default: current working dir)
        """
        path = runez.resolved_path(path, base=base)
        if path not in self.config_paths:
            settings_file = SettingsFile(self, path)
            self.config_paths.append(path)
            self.children.append(settings_file)
            include = settings_file.include
            if include:
                for ipath in include:
                    self._add_config(ipath, base=settings_file.folder)

    def resolved_definition(self, key, package_spec=None, default=None):
        """
        :param str key: Key to look up
        :param system.PackageSpec|None package_spec: Optional associated pypi package spec
        :param default: Optional default value (takes precendence over system.SETTINGS.defaults only)
        :return Definition|None: Definition corresponding to 'key', if any
        """
        definition = self.cli.get_definition(key)
        if definition:
            return definition
        for child in self.children:
            definition = child.resolved_definition(key, package_spec=package_spec)
            if definition is not None:
                return definition
        if default:
            return Definition(default, "default", key)
        return self.defaults.get_definition("default.%s" % key)

    def resolved_value(self, key, package_spec=None, default=None):
        """
        :param str key: Key to look up
        :param system.PackageSpec|None package_spec: Optional associated pypi package spec
        :param default: Default value to return if 'key' is not defined
        :return: Value corresponding to 'key' in this settings file, if any
        """
        definition = self.resolved_definition(key, package_spec=package_spec)
        if definition is not None:
            return definition.value
        return default

    def get_definition(self, key):
        """
        :param str key: Key to look up
        :return Definition|None: Top-most definition found, if any
        """
        definition = self.cli.get_definition(key)
        if definition:
            return definition
        for child in self.children:
            definition = child.get_definition(key)
            if definition is not None:
                return definition
        return self.defaults.get_definition(key)

    def get_value(self, key, default=None):
        """
        :param str key: Key to look up
        :param default: Default value to return if 'key' is not defined
        :return: Value corresponding to 'key' in this settings file, if any
        """
        value = self.get_definition(key)
        if value is not None:
            return value.value
        return default

    @property
    def index(self):
        """
        :return str: Optional pypi index to use
        """
        return self.get_value("index")

    def represented(self, include_defaults=True):
        """
        :param bool include_defaults: When True, include representation of defaults as well
        :return str: Human readable representation of these settings
        """
        result = [
            "settings:",
            "  base: %s" % short(self.base.path),
        ]
        if self.index:
            result.append("  index: %s" % self.index)
        result.append("")
        result.append("  config:")
        result.append(self.cli.represented())
        for child in self.children:
            result.append(child.represented())
        if include_defaults:
            result.append(self.defaults.represented())
        return "\n".join(result).strip()


system.SETTINGS = Settings()
