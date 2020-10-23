=======
History
=======

2.1.1 (2020-10-23)
------------------

* Much lighter pex package (400K, down from 5MB)

* Install ``virtualenv`` on the fly when needed

* Better bootstrap, multiple fall-back ways to query pypi


2.1.0 (2020-10-16)
------------------

* Bumped minor version to get pickley <2.0.7 unstuck


2.0.14 (2020-10-15)
-------------------

* Workaround for https://github.com/tox-dev/tox/issues/1689


2.0.13 (2020-10-13)
-------------------

* Clean byte-compile artifacts after ``--version`` sanity check was called


2.0.12 (2020-10-13)
-------------------

* Remove any left-over byte-compile artifacts when ``--no-compile`` is used


2.0.11 (2020-10-13)
-------------------

* ``compileall`` packaged venvs by default (can be turned off via ``--no-compile``)

* Disable OSX ARM explicitly for now


2.0.10 (2020-10-12)
-------------------

* Respect ``--python`` CLI flag in ``package`` command


2.0.9 (2020-10-07)
------------------

* Prevent OSX framework python from polluting created venvs

* Show why sanity check failed in ``package`` command


* Publish with 3.8


2.0.7 (2020-09-02)
------------------

* Properly compare versions when auto-determining desired version


2.0.6 (2020-06-18)
------------------

* Corrected determination of invoker python on Linux

* Corrected bootstrap case when py3 becomes available after initial install

* Corrected ``package --symlink``


2.0.1 (2020-06-11)
------------------

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
