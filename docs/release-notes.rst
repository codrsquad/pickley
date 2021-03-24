=============
Release notes
=============

2.4.0 (2021-03-23)
------------------

* Prefer pyenv-installed pythons, when configured/available

* Using pex 2.1.34


2.3.2 (2021-02-11)
------------------

* Keep ``virtualenv.pyz`` for 1 day in cache

* Create venvs with a bootstrapped virtualenv when module ``venv`` is not available

* Using latest pex 2.1.30


2.2.11 (2021-01-20)
-------------------

* Added progress spinner

* Remove ``PYTHONPATH`` from env vars if present, use ``pip install --isolated``

* Generalized package contents inspection

* Respect ``VIRTUALENV_PIP`` env var if present

* Look into ``.dist-info`` only for entry points


2.2.4 (2021-01-07)
------------------

* Using latest pex 2.1.24

* Perform potential ``git clone`` while holding the installation lock

* Added ``config`` path output to ``base`` sub-command

* Allow to install from folder as well (in addition to git url)

* Special case for ansible_

* Moved repo to github/codrsquad


2.2.3 (2020-11-17)
------------------

* Allow installation from git repo url

* Respect --no-compile for all package implementations

* Removed package --no-sanity-check CLI flag

* ``base`` command can now show path of meta folder and audit.log

* Use bundled virtualenv only when running from a venv (not from a pex)

* Error out early if an unusable python is requested, explicitly accept ``invoker`` to represent python pickley was packaged with


2.1.9 (2020-11-06)
------------------

* Added ``facultative`` setting, allowing to optionally install packages (if not there already)

* Removed python2 support for packaging via ``pex``

* Removed default sanity check, use ``pickley package --sanity-check=--version`` to enable it explicitly

* Use logging level INFO by default for ``pickley package``

* Always use ``virtualenv`` instead of the builtin ``venv`` module

* Upgraded to pex 2.1.20 when running with python3

* Simplified to using seconds for ``install_timeout`` and ``version_check_delay``

* Much lighter pex package (500K, down from 5MB)

* Better bootstrap, multiple fall-back ways to query pypi

* Automatically "heal" installed venvs (when underlying python is moved for example)

* Moved to Github Actions instead of Travis


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


.. _ansible: https://pypi.org/project/ansible/
