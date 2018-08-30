Automate install/upgrade/packaging of standalone python CLIs
============================================================

.. image:: https://img.shields.io/pypi/v/pickley.svg
    :target: https://pypi.org/project/pickley/
    :alt: Version on pypi

.. image:: https://travis-ci.org/zsimic/pickley.svg?branch=master
    :target: https://travis-ci.org/zsimic/pickley
    :alt: Travis CI

.. image:: https://codecov.io/gh/zsimic/pickley/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/zsimic/pickley
    :alt: codecov

.. image:: https://img.shields.io/pypi/pyversions/pickley.svg
    :target: https://github.com/zsimic/pickley
    :alt: Python versions tested (link to github project)


Overview
========

**pickley** allows to install and keep up-to-date standalone pip-installable python CLIs such as twine, tox, etc.
A bit like brew_ or apt_, but based solely on pypi_

Features:

- Any pypi_ package that has ``console_scripts`` entry point can be installed and kept up-to-date

- Aims to work with zero configuration (but configuration is possible):

    - entirely portable, installations are performed in the same folder where **pickley** resides,
      drop it in ``/usr/local/bin`` and all the stuff you install with it will also be there

    - latest non pre-release version from pypi is used

- Packaging is done via pex_ by default, but virtualenv_ or shiv_ can be used to (more possible in the future)

- Commands:

    - ``check``: exit with code 0 if specified package(s) are up-to-date

    - ``install``: install specified package(s)

    - ``list``: list installed packages via **pickley**, in folder where it resides (not globally)

    - ``package``: can be used to simplify packaging of python project via pex_ or shiv_, for internal use


Example
=======

Once you have pickley_, you can get other python CLIs and use them as standalone programs, for example::

    $ which pickley
    ~/.local/bin/pickley

    $ pickley install twine
    Installed twine 1.11

    $ which twine
    ~/.local/bin/twine

    $ twine --version
    twine version 1.11.0

    $ pickley list
    base: ~/.local/bin
    tox 3.1.2
    twine 1.11


.. _pickley: https://pypi.org/project/pickley/

.. _pypi: https://pypi.org/

.. _pip: https://pypi.org/project/pip/

.. _pex: https://pypi.org/project/pex/

.. _virtualenv: https://pypi.org/project/virtualenv/

.. _shiv: https://pypi.org/project/shiv/

.. _brew: https://brew.sh/

.. _apt: https://en.wikipedia.org/wiki/APT_(Debian)


Installation
============

Install from github releases
----------------------------

- Go to https://github.com/zsimic/pickley/releases/latest
- Download pickley from there (1st link), and drop it in ``~/.local/bin`` for example (a folder in your PATH)

Install via bash script
-----------------------

Run::

    pickley_latest=`curl -s https://github.com/zsimic/pickley/releases/latest | egrep -o 'tag/[^"]+' | sed 's!tag/!!'`
    curl -sLo ~/.local/bin/pickley https://github.com/zsimic/pickley/releases/download/$pickley_latest/pickley

    curl -sLo ~/.local/bin/pickley https://github.com/zsimic/pickley/releases/download/`curl -s https://github.com/zsimic/pickley/releases/latest | egrep -o 'tag/[^"]+' | sed 's!tag/!!'`/pickley

    u=https://github.com/zsimic/pickley/releases curl -sLo ./pickley $u/download/`curl -s $u/latest | egrep -o 'tag/[^"]+' | cut -d/ -f2`/pickley


Install from source
-------------------

Run (you will need tox_)::

    git clone https://github.com/zsimic/pickley.git
    cd pickley
    tox -e package
    cp .tox/package/pickley ~/.local/bin/


.. _tox: https://pypi.org/project/tox/
