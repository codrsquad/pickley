For developers
==============

* Virtual environment for local iteration (venv is created in ``.venv`` and can by used in PyCharm)::

    tox -re venv                    # To create the .venv
    .venv/bin/pickley --version
    .venv/bin/pickley --help
    .venv/bin/pickley install mgit
    .venv/bin/pickley list
    .venv/root/mgit --version

* Package up pickley for deployment::

    tox -e package
    head -1 .tox/package/pickley

