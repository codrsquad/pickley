Contributions are welcome!

tox_ is used for building and testing, ``setup.py`` is kept simple thanks to setupmeta_.

Development
===========

To get going locally, simply do this::

    git clone https://github.com/zsimic/pickley.git
    cd pickley

    # Conveniently create the .venv via tox
    tox -re venv
    .venv/bin/pickley --help

    # ./.venv/ is a regular venv, you can use it with PyCharm etc
    source .venv/bin/activate
    which pickley
    deactivate

    # You can use pickley directly from the dev .venv/
    .venv/bin/pickley --help
    .venv/bin/pickley install mgit
    .venv/bin/pickley list
    .venv/bin/pickley config

    # Base is .venv/root/ when running in dev mode
    .venv/root/mgit --version

    # Package it up as a pex
    tox -e package
    head -1 .tox/package/dist/pickley


Running the tests
=================

To run the tests, simply run ``tox``, this will run tests against all python versions you have locally installed.
You can use pyenv_ for example to get python installations.

Run:

* ``tox -e py39`` (for example) to limit test run to only one python version.

* ``tox -e style`` to run style checks only

* After running ``tox``,
  you can see test coverage report: ``open .tox/test-reports/htmlcov/index.html``


.. _pyenv: https://github.com/pyenv/pyenv

.. _tox: https://github.com/tox-dev/tox

.. _setupmeta: https://pypi.org/project/setupmeta/
