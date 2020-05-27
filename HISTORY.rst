=======
History
=======

2.0.0 (2020-06-01)
------------------

* Strictly default to python 3 (was sticking to system python before)

* Refactored, simplified code

  * Not using temporary build venvs anymore

  * 3x faster now when installing average sized projects

  * 1200 lines of code (was 1500)


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
