Base
====

Pickley does everything relative to its ``<base>`` folder.
The ``<base>`` folder is:

- the folder where pickley is installed in normal operation
- ``.venv/root`` when running from a dev venv (including in debugger)
- ``--build`` folder when running ``pickley package``
- can be overridden by env var ``PICKLEY_ROOT`` (unusual, for testing)


Settings
========

A subset of the configuration is referred to as ``settings``, these are just 3 values
that the user can easily override via corresponding CLI flags:

- ``delivery``: delivery method to use (one of: ``symlink`` or ``wrap``, descendant of class ``DeliveryMethod``)
- ``index``: pypi index to use
- ``python``: desired python version to use, default: same python as pickley is installed with


Configuration
=============

Pickley loads a configuration file (if present) from:

- ``--config`` command line argument
- ``<base>/.pickley/config.json`` by default

More configuration files can be included, via an ``include`` directive.
First definition found wins. Search order is:

- Command line flags (each Settings_ above has a corresponding CLI flag)
- configuration file's ``pinned`` section
- configuration file's top-level definition
- hardcoded default

For example, which ``delivery`` method should be used to install ``<installable>``?:

- if ``--delivery`` provided -> it wins, we use that
- ``pinned/<installable>/delivery`` entry in configuration, if there is one
- ``delivery`` top-level definition in configuration, if there is one
- finally, use hardcoded default if none of the above found

Configuration files are json as their name implies, here's an example::

    {
      "include": "foo.json",
      "bundle": {
        "dev": "tox twine"
      },
      "pinned": {
        "mgit": "1.1.2",
        "tox": {
            "delivery": "wrap",
            "index": "....",
            "python": "3.9",
            "version": "3.2.1"
        }
      },
      "index": "https://pypi-mirror.mycompany.net/pypi",
      "delivery": "wrap",
      "pyenv": "~/.pyenv",
      "install_timeout": 1800,
      "version_check_delay": 300
    }


The above means:

- Include a secondary configuration file (relative paths are relative to configuration file stating the ``include``)

- Define a "bundle" called ``dev`` (convenience for installing multiple things at once via ``pickley install bundle:dev``)

- Pin version to use for ``mgit``, pin every possible setting for ``tox``

- Use a custom pypi index

- Use the ``wrap`` delivery mode by default (which will create a self-upgrading wrapper,
  checking for new versions and auto-installing them every time you invoke corresponding command)

- Look for pyenv installations in ``~/.pyenv``

- ``install_timeout``how long to wait (in seconds) before considering an installation as failed

- ``version_check_delay`` how frequently to check (in seconds) for new latest versions on pypi
