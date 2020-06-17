=======
History
=======

2.0.4 (2020-06-16)
------------------

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


1.9.10 (2020-01-20)
-------------------

* ``package`` command enhancements


1.9.8 (2019-11-17)
------------------

* Use builtin venv module with py3

* Package projects without entry point as well


1.9.6 (2019-10-06)
------------------

* Bug fixes


1.9.4 (2019-09-12)
------------------

* ``compileall`` ran on packaged venvs are
