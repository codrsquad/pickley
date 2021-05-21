Automate installation of standalone python CLIs
===============================================

.. image:: https://img.shields.io/pypi/v/pickley.svg
    :target: https://pypi.org/project/pickley/
    :alt: Version on pypi

.. image:: https://github.com/codrsquad/pickley/workflows/Tests/badge.svg
    :target: https://github.com/codrsquad/pickley/actions
    :alt: Tested with Github Actions

.. image:: https://codecov.io/gh/codrsquad/pickley/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/codrsquad/pickley
    :alt: Test code codecov

.. image:: https://img.shields.io/pypi/pyversions/pickley.svg
    :target: https://github.com/codrsquad/pickley
    :alt: Python versions tested (link to github project)


Overview
========

**pickley** allows to install and keep up-to-date standalone pip-installable python CLIs
such as tox_, twine_, etc. A bit like brew_ or apt_, but based solely on pypi_

It is similar to pipx_, but supports any python (including py2, up to version 2.4.6), offers self-auto-upgrade, and
can ``package`` folders as well (for deployment, as venv or pex_ currently).

It can work out of the box, **without any configuration**:

- **pickley** is portable, it will run and install other CLIs in the same folder it's running from
  (drop it in ``~/.local/bin`` or ``/usr/local/bin`` for example)

- All pypi packages with ``console_scripts`` entry point(s) can be immediately installed

- Latest non-prerelease pypi version will be installed by default
  (can be pinned via explicit pin ``pickley install foo==1.0``, or via configuration)

With **some configuration**, the following becomes possible:

- You can pin what version to install, what python to use etc, per pypi package

- You can define ``bundle``-s: names that install several pypi packages at once,
  for example: you could define a ``bundle:dev`` to install ``tox pipenv pre-commit``

- You can use a custom pypi server index (pip's default is respected by default)

- You can use the **symlink** delivery method, which will use symlinks instead of self-upgrading wrapper


Example
=======

Once you have pickley_, you can get other python CLIs and use them as standalone programs, for example::

    # One-liner to grab pickley, and drop it in ~/.local/bin
    $ curl -sLo ~/.local/bin/pickley `curl -s https://pypi.org/pypi/pickley/json | grep -Eo '"download_url":"([^"]+)"' | cut -d'"' -f4`
    $ chmod a+x ~/.local/bin/pickley

    # Double-check you do have ~/.local/bin in your PATH
    $ which -a pickley
    ~/.local/bin/pickley

    $ pickley base
    ~/.local/bin

    $ pickley install tox twine
    Installed tox v3.21.4 in 6 seconds 501 ms
    Installed twine v3.3.0 in 6 seconds 901 ms

    $ which tox
    ~/.local/bin/tox

    $ tox --version
    tox version 3.21.4

    $ pickley list
    | Package    | Version |
    -------------|----------
    | tox        | 3.21.4  |
    | twine      | 3.3.0   |


Configuration
=============

See config_


Packaging
=========

**pickley** can also be used to easily package your CLI project for delivery, example tox_ section for a project called ``foo``::


    # Package ourselves up, this will produce a .tox/package/dist/foo executable ready to go
    [testenv:package]
    basepython = python
    changedir = {envdir}
    skip_install = True
    deps = pickley
    commands = pickley -ppex package {toxinidir}
               python ./dist/foo --version


pickley packages itself like this for example.
See ``pickley package --help`` for options, by default:

- Produced package(s) (one per entry point) are dropped in ``./dist`` (configurable via ``--dist`` or ``-d``)

- Used wheels are dropped in ``./build`` (configurable via ``--build`` or ``-b``)

- We run ``./dist/foo --version`` here as a sanity check against our freshly produced package

- Using tox's ``changedir = {envdir}`` allows to simplify invocations
  (relative paths are relative to ``{envdir}``, which is ``.tox/package`` in this case)

- Using ``skip_install = True`` just for speedup (the project itself is not needed within the 'pacakage' tox env)

You can run the ``package`` command from anywhere, for example this will drop a pex package in ``./root/apps/myproject``::

    pickley -ppex package path/to/myproject -droot/apps/myproject


Features
========

- Any pypi_ package that has ``console_scripts`` entry point can be installed and kept up-to-date

- Aims to work with zero configuration (but configuration is possible):

    - entirely portable, installations are performed in the same folder where **pickley** resides,
      drop it in ``~/.local/bin`` and all the stuff you install with it will also be there

    - latest non pre-release version from pypi is used

- Commands:

    - ``check``: exit with code 0 if specified package(s) are up-to-date

    - ``install``: install specified package(s)

    - ``list``: list installed packages via **pickley**, in folder where it resides (not globally)

    - ``package``: can be used to simplify packaging of python project via pex_ or shiv_, for internal use


Installation
============

Install from github releases
----------------------------

- Go to https://github.com/codrsquad/pickley/releases/latest
- Download pickley from there (1st link), and drop it in ``~/.local/bin`` for example (or any folder in your PATH)

You can do that with these commands::

    curl -sLo ~/.local/bin/pickley `curl -s https://pypi.org/pypi/pickley/json | grep -Eo '"download_url":"([^"]+)"' | cut -d'"' -f4`
    chmod a+x ~/.local/bin/pickley


Install from source
-------------------

Run (you will need tox_)::

    git clone https://github.com/codrsquad/pickley.git
    cd pickley
    tox -e package
    cp .tox/package/pickley ~/.local/bin/


.. _pickley: https://pypi.org/project/pickley/

.. _pypi: https://pypi.org/

.. _pip: https://pypi.org/project/pip/

.. _pipx: https://pypi.org/project/pipx/

.. _pex: https://pypi.org/project/pex/

.. _virtualenv: https://pypi.org/project/virtualenv/

.. _shiv: https://pypi.org/project/shiv/

.. _brew: https://brew.sh/

.. _apt: https://en.wikipedia.org/wiki/APT_(Debian)

.. _tox: https://pypi.org/project/tox/

.. _twine: https://pypi.org/project/twine/

.. _config: docs/config.rst
