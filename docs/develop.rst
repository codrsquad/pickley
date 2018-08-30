For developers
==============

* Virtual environment for local iteration (venv is created in ``.venv`` and can by used in PyCharm)::

    tox -e venv
    source .venv/bin/activate
    which pickley
    pickley --version
    pickley --help
    pickley install twine
    pickley list
    .venv/root/twine --version
    deactivate

* Package up pickley for deployment::

    tox -e package
    head -1 .tox/package/pickley


Find all config key usages::

    ag '\.(get_definition|get_value|contents\.get)\(["'"'"']'

    # or if you don't have 'ag':
    egrep -r '\.(get_definition|get_value|contents\.get)\(["'"'"']' src/ tests/

