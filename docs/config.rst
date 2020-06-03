Base
====

Pickley does everything relative to its ``<base>`` folder.
The ``<base>`` folder is:

- the folder where pickley is installed in normal operation
- ``.venv/root`` when running in debugger, or from dev venv
- ``--build`` folder when running ``pickley package``
- can be overridden by env var ``PICKLEY_ROOT`` (unusual, for testing)


Settings
========

A subset of the configuration is referred to as `settings`, these are just 3 values
that the user can easily override via corresponding CLI flags:

- ``delivery``: delivery method to use (one of: ``symlink`` or ``wrap``, descendant of class ``DeliveryMethod``)
- ``index``: pypi index to use
- ``python``: desired python version to use, default: same python as pickley is using


Configuration
=============

Pickley loads a configuration file (if present) from:

- ``--config`` command line argument
- ``<base>/.pickley/config.json`` by default

More configuration files can be included, via an ``include`` directive.
First definition found wins. Search order is:

- Command line flags (each "configurable" above has a corresponding CLI flag)
- configuration file's ``pinned`` section
- configuration file's ``default`` section
- hardcoded default

For example, which ``delivery`` method should be used to install ``<installable>``?:

- if ``--delivery`` provided -> it wins, we use that
- ``pinned/<installable>/delivery`` entry in configuration, if there is one
- ``default/delivery`` entry in configuration, if there is one
- finally, use hardcoded default if none of the above found

Configuration is internally held by ``PickleyConfig`` object, which has the following top level definitions:

- ``include``: optional path to another configuration to include
- bundle: name -> list
- pinned: pkg -> (str | configurable + version)
- default: configurable -> value

Moved/removed:
- index -> default/index
- channel
- select

Configuration files are json as their name implies, here's an example::

    {
      "include": "foo.json",
      "bundle": {
        "dev": "tox twine"
      },
      "pinned": {
        "tox": "3.2.1",
        "tox": {
            "delivery": "wrap",
            "index": "....",
            "packager": "venv",
            "python": "3.7",
            "version": "3.2.1"
        }
      },
      "index": "https://pypi-mirror.mycompany.net/pypi",
      "delivery": "wrap"
    }


The above means:

- Use a custom pypi index

- Include a secondary configuration file (relative paths are relative to configuration file stating the ``include``)

- Define a "bundle" called ``dev`` (convenience for installing multiple things at once via ``pickley install bundle:dev``)

- Define a channel called "stable" specifying which versions of which pypi packages should be used

- Use the ``wrap`` delivery mode by default (which will create a self-upgrading wrapper,
  checking for new versions and auto-installing them every time you invoke corresponding command)

- Customize delivery mode for ``twine`` (use regular ``symlink`` here in this case, instead of default auto-upgrading ``wrap``)


Layout
======

Sample::

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
        "python_installs": "~/.pyenv/versions",
        "install_timeout": 30,
        "version_check_delay": 10
        "select": {
            "twine": {
                "channel": "latest",
                "delivery": "symlink",
                "packager": "pex",
            }
        }
    }

