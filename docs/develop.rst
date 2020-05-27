For developers
==============

* Virtual environment for local iteration (venv is created in ``.venv`` and can by used in PyCharm)::

    tox -e venv
    source .venv/bin/activate
    which pickley
    pickley --version
    pickley --help
    pickley install mgit
    pickley list
    .venv/root/mgit --version
    deactivate

* Package up pickley for deployment::

    tox -e package
    head -1 .tox/package/pickley


