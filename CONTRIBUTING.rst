Contributions are welcome!

tox_ is used for building and testing, ``setup.py`` is kept simple thanks to setupmeta_.

Development
===========

To get going locally, simply do this::

    git clone https://github.com/zsimic/pickley.git
    cd pickley

    tox -e venv

    # You have a venv now in ./.venv, use it, open it with pycharm etc
    source .venv/bin/activate
    which python
    which pickley
    pickley install tox
    pickley list

    deactivate


You can also get into pickley's virtualenv via ``tox -e venv bash``, use ``exit`` (or ``CTRL+D``) to exit in that case (not ``deactivate``).


Running the tests
=================

To run the tests, simply run ``tox``, this will run tests against all python versions you have locally installed.
You can use pyenv_ for example to get python installations.

Run:

* ``tox -e py27`` (for example) to limit test run to only one python version.

* ``tox -e style`` to run style checks only

* etc


Test coverage
=============

Run ``tox ; tox -e coverage``, then open ``.tox/_reports/coverage/index.html``


.. _pyenv: https://github.com/pyenv/pyenv

.. _tox: https://github.com/tox-dev/tox

.. _setupmeta: https://pypi.org/project/setupmeta/
