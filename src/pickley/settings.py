# coding=utf-8
"""
Simple json configuration system

<base>: installation folder, ex ~/.local/bin

The following locations will be examined for config (in this order, first value found wins):
- ~/.config/pickley.json
- <base>/.pickley.json

tree <base>
├── .pickley/                       # Folder where pickley will build/manage/track installations
│   ├── audit.log                   # Activity is logged here
│   ├── tox/
│   │   ├── .current.json           # Currently installed version
│   │   ├── .latest.json            # Latest version as determined by querying pypi
│   │   ├── dist/                   # Temp folder used during packaging
│   │   └── tox-2.9.1/              # Actual installation, as packaged by pickley
├── .pickley.json                   # Optional config provided by user
├── pickley                         # pickley itself
└── tox -> .pickley/tox/2.9.1/...   # Produced exe, can be a symlink or a small wrapper exe (to ensure up-to-date)

{
    "bundle": {
        "mybundle": "tox twine"
    },
    "channel": {
        "stable": {
            "tox": "1.0"
        }
    },
    "default": {
        "channel": "latest",
        "delivery": "wrap, or symlink, or copy",
        "packager": "venv"
    },
    "delivery": {
        "wrap": "logfetch mgit"
    },
    "include": [
        "~/foo/pickley.json"
    ],
    "index": "https://pypi.org/",
    "select": {
        "twine": {
            "channel": "latest",
            "delivery": "symlink",
            "packager": "pex",
        }
    }
}
"""

import json
import os

import six

from pickley import short, system


def same_type(t1, t2):
    """
    :return bool: True if 't1' and 't2' are of equivalent types
    """
    if isinstance(t1, six.string_types) and isinstance(t2, six.string_types):
        return True
    return type(t1) == type(t2)


def meta_folder(path):
    """
    :param str path: Path to folder to use
    :return FolderBase: Associated object
    """
    return FolderBase(os.path.join(path, system.DOT_PICKLEY), name="meta")


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
                brief = system.represented_args(value, separator=", ")
                if len(brief) < 90:
                    result.append("%s%s: [%s]" % (indent, short(key), brief))
                    continue
            if isinstance(value, (dict, list)):
                result.append("%s%s:" % (indent, short(key)))
                add_representation(result, value, indent="  %s" % indent)
            else:
                result.append("%s%s: %s" % (indent, short(key), short(value)))
        return
    result.append("%s- %s" % (indent, short(data)))


class JsonSerializable:
    """
    Json serializable object
    """

    _path = None  # type: str # Path where this file should be stored, if any
    _source = None  # type: str # Where data came from

    def __repr__(self):
        return self._source or "no source"

    @classmethod
    def from_json(cls, path):
        """
        :param str path: Path to json file
        :return cls: Deserialized object
        """
        result = cls()
        result.load(path)
        return result

    def set_from_dict(self, data, source=None):
        """
        :param dict data: Set this object from deserialized 'dict'
        :param source: Source where 'data' came from
        """
        if source:
            self._source = source
        if not data:
            return
        for key, value in data.items():
            key = key.replace("-", "_")
            if not hasattr(self, key):
                system.debug("%s is not an attribute of %s", key, self.__class__.__name__)
                continue
            attr = getattr(self, key)
            if attr is not None and not same_type(value, attr):
                system.debug(
                    "Wrong type %s for %s.%s in %s, expecting %s", type(value), self.__class__.__name__, key, self._source, type(attr)
                )
                continue
            setattr(self, key, value)

    def reset(self):
        """
        Reset all fields of this object to class defaults
        """
        for name in self.__dict__:
            if name.startswith("_"):
                continue
            attr = getattr(self, name)
            setattr(self, name, attr and attr.__class__())

    def to_dict(self):
        """
        :return dict: This object serialized to a dict
        """
        result = {}
        for name in self.__dict__:
            if name.startswith("_"):
                continue
            name = name.replace("_", "-")
            attr = getattr(self, name)
            result[name] = attr.to_dict() if isinstance(attr, JsonSerializable) else attr
        return result

    def load(self, path=None):
        """
        :param str|None path: Load this object from file with 'path' (default: self._path)
        """
        self.reset()
        if path:
            self._path = path
            self._source = short(path)
        if not self._path:
            return
        data = JsonSerializable.get_json(self._path)
        if not data:
            return
        self.set_from_dict(data)

    def save(self, path=None):
        """
        :param str|None path: Save this serializable to file with 'path' (default: self._path)
        """
        JsonSerializable.save_json(self.to_dict(), path or self._path)

    @staticmethod
    def save_json(data, path):
        """
        :param dict|list|None data: Data to serialize and save
        :param str path: Path to file where to save
        """
        if data is None or not path:
            return
        try:
            path = system.resolved_path(path)
            system.ensure_folder(path)
            if system.DRYRUN:
                system.debug("Would save %s", short(path))
            else:
                with open(path, "wt") as fh:
                    json.dump(data, fh, sort_keys=True, indent=2)

        except Exception as e:
            system.warning("Couldn't save %s: %s" % (short(path), e))

    @staticmethod
    def get_json(path, default=None):
        """
        :param str path: Path to file to deserialize
        :param dict|list default: Default if file is not present, or if it's not json
        :return dict|list: Deserialized data from file
        """
        path = system.resolved_path(path)
        if not path or not os.path.exists(path):
            return default

        try:
            with open(path, "rt") as fh:
                data = json.load(fh)
                if default is not None and type(data) != type(default):
                    system.debug("Wrong type %s for %s, expecting %s" % (type(data), short(path), type(default)))
                return data

        except Exception as e:
            system.warning("Invalid json file %s: %s" % (short(path), e))
            return default


class FolderBase(object):
    """
    This class allows to more easily deal with folders
    """

    def __init__(self, path, name=None):
        """
        :param str path: Path to folder
        :param str|None name: Name of this folder (defaults to basename of 'path')
        """
        self.path = system.resolved_path(path)
        self.name = name or os.path.basename(path)

    def relative_path(self, path):
        """
        :param str path: Path to relativize
        :return str: 'path' relative to self.path
        """
        return os.path.relpath(path, self.path)

    def full_path(self, *relative):
        """
        :param list(str) *relative: Relative components
        :return str: Full path based on self.path
        """
        return os.path.join(self.path, *relative)

    def __repr__(self):
        return "%s: %s" % (self.name, short(self.path))


class Definition(object):
    """
    Defined value, with origin where the value came from
    """

    def __init__(self, value, source, key):
        """
        :param value: Actual value
        :param SettingsFile source: Where value was defined
        :param str key: Under what key it was defined
        """
        self.value = value
        self.source = source
        self.key = key

    def __repr__(self):
        return "%s:%s" % (short(self.source), self.key)


class SettingsFile:
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
        self.path = short(path) or name
        self.folder = system.parent_folder(path)
        self._contents = None

    def __repr__(self):
        return self.path

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
                        result.extend(system.flattened(bundle.value, separator=" "))
                        continue
                result.append(name)
        return system.flattened(result, separator=" ")

    def flatten(self, key, separator=None, direct=False):
        if not self._contents:
            return
        node = self._contents.get(key)
        if not node:
            return
        if direct:
            self._contents[key] = system.flattened(node, separator=separator)
            return
        result = {}
        for name, value in node.items():
            result[name] = system.flattened(value, separator=separator)
        self._contents[key] = result

    @property
    def contents(self):
        """
        :return dict: Deserialized contents of settings file
        """
        if self._contents is None:
            self.set_contents(JsonSerializable.get_json(self.path, default={}))
        return self._contents

    @property
    def include(self):
        """
        :return list(str): Optional list of other settings files to include
        """
        return self.contents.get("include")

    def resolved_definition(self, key, package_name=None):
        """
        :param str key: Key to look up
        :param str|None package_name: Optional associated package name
        :return Definition|None: Definition corresponding to 'key' in this settings file, if any
        """
        if not key:
            return None
        if package_name:
            definition = self.get_definition("select.%s.%s" % (package_name, key))
            if definition:
                return definition
            main = self.contents.get(key)
            if isinstance(main, dict):
                for name, values in main.items():
                    if isinstance(values, dict):
                        if package_name in values:
                            return Definition(name, self, "%s.%s" % (key, name))
                    elif hasattr(values, "split"):
                        if package_name in values.split():
                            return Definition(name, self, "%s.%s" % (key, name))
        return self.get_definition("default.%s" % key)

    def get_definition(self, key):
        """
        :param str key: Key to look up
        :param str|None package_name: Optional associated package name
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
                system.debug("'%s' is of type %s (not a dict) in '%s'", prefix, type(definition.value), short(self.path))
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


class Settings:
    """
    Collection of settings files
    """

    def __init__(self, base=None, config=None):
        """
        :param str|None base: Base folder to use
        :param list|None config: Optional configuration files to load
        """
        self.set_base(base)
        self.cli = SettingsFile(self, name="cli")
        self.set_cli_config()
        self.defaults = SettingsFile(self, name="defaults")
        self.defaults.set_contents(
            default=dict(
                channel=system.DEFAULT_CHANNEL,
                delivery=system.DEFAULT_DELIVERY,
                packager=system.DEFAULT_PACKAGER,
                python=system.PYTHON,
            ),
        )
        self.config = config
        self.paths = []
        self.children = []
        self.add(config)

    def __repr__(self):
        return "[%s] %s" % (len(self.children), self.base)

    def set_base(self, base):
        """
        :param str|FolderBase|None base: Folder to use as base for installations
        """
        if not base:
            base = os.environ.get("PICKLEY_ROOT")
        if not base:
            base = system.parent_folder(system.PROGRAM)
            if system.DOT_PICKLEY in base:
                # Don't consider bootstrapped .pickley/... as installation base
                i = base.index(system.DOT_PICKLEY)
                base = base[:i].rstrip("/")
            elif ".venv" in base:
                # Convenience for development
                base = system.parent_folder(base)
                base = os.path.join(base, "root")

        if isinstance(base, FolderBase):
            self.base = base
        else:
            self.base = FolderBase(base, name="base")

        self.meta = meta_folder(self.base.path)

    def set_cli_config(self, **entries):
        self.cli.set_contents(dict((k, v) for k, v in entries.items() if v))

    def add(self, paths, base=None):
        """
        :param list(str) paths: Paths to files to consider as settings
        :param str base: Base path to use to resolve relative paths
        """
        if not paths:
            return
        for path in paths:
            path = system.resolved_path(path, base=base or self.base.path)
            if path in self.paths:
                return
            settings_file = SettingsFile(self, path)
            self.paths.append(path)
            self.children.append(settings_file)
            if settings_file.include:
                self.add(settings_file.include, base=settings_file.folder)

    def resolved_definition(self, key, package_name=None):
        """
        :param str key: Key to look up
        :param str|None package_name: Optional associated package name
        :return Definition|None: Definition corresponding to 'key', if any
        """
        definition = self.cli.get_definition(key)
        if definition:
            return definition
        for child in self.children:
            definition = child.resolved_definition(key, package_name=package_name)
            if definition is not None:
                return definition
        return self.defaults.resolved_definition(key, package_name=package_name)

    def resolved_value(self, key, package_name=None, default=None):
        """
        :param str key: Key to look up
        :param str|None package_name: Optional associated package name
        :param default: Default value to return if 'key' is not defined
        :return: Value corresponding to 'key' in this settings file, if any
        """
        definition = self.resolved_definition(key, package_name=package_name)
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

    def resolved_packages(self, names):
        """
        :param list|tuple names: Names to resolve
        :return set: Resolved names
        """
        result = []
        if names:
            if hasattr(names, "split"):
                names = names.split()
            for name in names:
                if name.startswith("bundle:"):
                    bundle = self.get_value("bundle.%s" % name[7:])
                    if bundle:
                        result.extend(bundle)
                        continue
                result.append(name)
        return system.flattened(result)

    def current_names(self):
        """Yield names of currently installed packages"""
        result = []
        if os.path.isdir(self.meta.path):
            for fname in os.listdir(self.meta.path):
                fpath = os.path.join(self.meta.path, fname)
                if os.path.isdir(fpath):
                    if os.path.exists(os.path.join(fpath, ".current.json")):
                        result.append(fname)
        return result

    def represented(self):
        """
        :return str: Human readable representation of these settings
        """
        result = [
            "settings:",
            "  base: %s" % short(self.base.path),
            "  meta: %s" % short(self.meta.path),
        ]
        if self.index:
            result.append("  index: %s" % self.index)
        result.append("  config:")
        result.append(self.cli.represented())
        for child in self.children:
            result.append(child.represented())
        result.append(self.defaults.represented())
        return "\n".join(result)


SETTINGS = Settings()
