Automate installation of standalone python CLIs
===============================================

.. image:: https://img.shields.io/pypi/v/pickley.svg
    :target: https://pypi.org/project/pickley/
    :alt: Version on pypi

.. image:: https://github.com/codrsquad/pickley/workflows/Tests/badge.svg
    :target: https://github.com/codrsquad/pickley/actions
    :alt: Tested with Github Actions

.. image:: https://codecov.io/gh/codrsquad/pickley/branch/main/graph/badge.svg
    :target: https://codecov.io/gh/codrsquad/pickley
    :alt: Test code codecov

.. image:: https://img.shields.io/pypi/pyversions/pickley.svg
    :target: https://github.com/codrsquad/pickley
    :alt: Python versions tested (link to github project)


Overview
========

**pickley** allows to install and keep up-to-date standalone pip-installable python CLIs
such as tox_, hatch_, etc.

It is `similar to pipx`_, main difference being installed CLIs automatically self-upgrade
as you use them.

It can work out of the box, **without any configuration**:

- **pickley** is portable, it will run and install other CLIs in the same folder it's running from
  (drop it in ``~/.local/bin`` or ``/usr/local/bin`` for example)

- All pypi packages with ``console_scripts`` entry point(s) can be immediately installed

- Latest non-prerelease pypi version will be installed by default

- Any specification acceptable to ``pip install`` can be stated, for example::

    pickley install tox  # track latest version

    pickley install 'tox>0a0'  # track pre-releases as well

    pickley install 'tox<4'  # track latest version that is strictly less than v4

    pickley install tox==3.24.3  # pin to a specific version

    pickley install tox~=3.28  # track version range

    pickley install git+https://...@some-branch  # track a git branch


With **some configuration**, the following becomes possible:

- You can pin what version to install, what python to use etc, per pypi package

- You can define ``bundle``-s: names that install several pypi packages at once,
  for example: you could define a ``bundle:dev`` to install ``tox pipenv pre-commit``

- You can use a custom pypi server index (pip's default is respected by default)

- You can use the **symlink** delivery method, which will use symlinks instead of self-upgrading wrapper


Example
=======

Once you have ``pickley``, you can get other python CLIs and use them as standalone programs, for example::

    # One-liner to grab pickley, and drop it in ~/.local/bin
    $ curl -fsSL https://raw.githubusercontent.com/codrsquad/pickley/main/src/pickley/bstrap.py | /usr/bin/python3 -

    # Double-check you do have ~/.local/bin in your PATH
    $ which -a pickley
    ~/.local/bin/pickley

    $ pickley base
    ~/.local/bin

    $ pickley install install tox 'hatch<2'
    Installed tox v4.21.2 in 1 second 4 ms
    Installed hatch v1.12.0 in 1 second 791 ms

    $ which tox
    ~/.local/bin/tox

    $ tox --version
    4.21.2 from .../.pk/tox-4.21.2/...

    $ pickley list
    | Package | Version | PM | Python           |
    ----------|---------|----|-------------------
    | hatch   | 1.12.0  | uv | /usr/bin/python3 |
    | tox     | 4.21.2  | uv | /usr/bin/python3 |
    | uv      | 0.4.20  | uv | /usr/bin/python3 |

    $ pickley list -v
    | Package | Version | PM | Python           | Delivery | Track   |
    ----------|---------|----|------------------|----------|----------
    | hatch   | 1.12.0  | uv | /usr/bin/python3 | wrap     | hatch<2 |
    | tox     | 4.21.2  | uv | /usr/bin/python3 | wrap     | tox     |
    | uv      | 0.4.20  | uv | /usr/bin/python3 | wrap     | uv      |


Configuration
=============

See config_


Features
========

- Any pypi_ package that has ``console_scripts`` entry point can be installed and kept up-to-date

- Aims to work with zero configuration (but configuration is possible):

    - entirely portable, installations are performed in the same folder where **pickley** resides,
      drop it in ``~/.local/bin`` and all the stuff you install with it will also be there

    - latest non pre-release version from pypi is used

Commands
========

    - ``install``: Install specified package(s)

    - ``uninstall``: Uninstall specified package(s)

    - ``upgrade``: Upgrade specified package(s)

    - ``check``: Exit with code 0 if specified package(s) are up-to-date

    - ``list``: List installed packages via **pickley**, in folder where it resides (not globally)

    - ``base``: Print the base folder where **pickley** resides

    - ``config``: Show current configuration

    - ``describe``: Describe a package spec (version and entrypoints)

    - ``diagnostics``: Show diagnostics info

    - ``run``: Run a python CLI (auto-install it if needed)

    - ``bootstrap``: Install pickley itself in target base folder


Installation
============

Install latest version in `~/.local/bin`
----------------------------------------

If you have uv_, you can use it to bootstrap **pickley**, for example in ``~/.local/bin``::

    $ uvx pickley bootstrap ~/.local/bin


Handy one-line using ``bash``::

    $ /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/codrsquad/pickley/main/get-pickley)"


Handy one-liner using python (see ``--help``, the script accepts a few options)::

    $ curl -fsSL https://raw.githubusercontent.com/codrsquad/pickley/main/src/pickley/bstrap.py | /usr/bin/python3 - --help


If you happen to have uv_ already installed (anywhere), you can run::

    $ uvx pickley bootstrap ~/.local/bin


Install from source
-------------------

Run (you will need tox_)::

    git clone https://github.com/codrsquad/pickley.git
    cd pickley
    uv venv
    uv pip install -r requirements.txt -r tests/requirements.txt -e .
    .venv/bin/pickley --help


.. _pypi: https://pypi.org/

.. _tox: https://pypi.org/project/tox/

.. _hatch: https://pypi.org/project/hatch/

.. _config: https://github.com/codrsquad/pickley/wiki/Config

.. _similar to pipx: https://github.com/codrsquad/pickley/wiki/Pickley-vs-pipx

.. _uv: https://pypi.org/project/uv/
