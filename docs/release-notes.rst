=============
Release notes
=============

2.1.8 (2020-10-30)
------------------

* Resolve path before invoking virtualenv.pyz


2.1.7 (2020-10-30)
------------------

* Corrected pex shebang


2.1.6 (2020-10-30)
------------------

* Removed default sanity check, use ``pickley package --sanity-check=--version`` to enable it explicitly

* Use logging level INFO by default for ``pickley package``

* Always use ``virtualenv`` instead of the builtin ``venv`` module

* Upgraded to pex 2.1.20 when running with python3


2.1.5 (2020-10-26)
------------------

* Properly clean ``.pyo`` files as well


2.1.4 (2020-10-26)
------------------

* Ensure latest pip, setuptools, wheel when using built-in venv module


2.1.3 (2020-10-26)
------------------

* Simplified to using seconds for ``install_timeout`` and ``version_check_delay``


2.1.2 (2020-10-26)
------------------

* Fine-tuned bootstrap case from pex


2.1.1 (2020-10-26)
------------------

* Much lighter pex package (400K, down from 5MB)

* Dynamically install/bootstrap ``virtualenv`` from its standalone zipapp

* Better bootstrap, multiple fall-back ways to query pypi

* Automatically "heal" installed venvs (when underlying python is moved for example)

* Moved to Github Actions instead of Travis


2.1.0 (2020-10-16)
------------------

* Bumped minor version to get pickley <2.0.7 unstuck


2.0.14 (2020-10-15)
-------------------

* Workaround for https://github.com/tox-dev/tox/issues/1689

* ``compileall`` packaged venvs by default (can be turned off via ``--no-compile``)

* Disable OSX ARM explicitly for now

* Respect ``--python`` CLI flag in ``package`` command

* Prevent OSX framework python from polluting created venvs

* Show why sanity check failed in ``package`` command

* Publish with python 3.8

* Properly compare versions when auto-determining desired version

* Corrected determination of invoker python on Linux

* Corrected bootstrap case when py3 becomes available after initial install

* Default to using ``/usr/bin/python3`` when possible (was sticking to system python before)

* Default to using self-upgrading wrapper instead of symlinks

* Refactored, simplified code

  * Not using temporary build venvs anymore, dropped support for relocating venvs

  * 3x faster now when installing average sized projects

  * Added commands: ``base``, ``config``, ``diagnostics``, ``upgrade``

  * Removed commands: ``copy``, ``move``, ``settings``


1.9.19 (2020-02-18)
-------------------

* Log more debug info on pip wheel run

* Corrected venv creation with py3

* Default to absolute venvs (non-relocatable), as relocatable venvs are tricky to keep working
