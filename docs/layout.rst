File layout
===========

File structure at ``<base>`` installation folder, example ``~/.local/bin``::

    tree <base>                         # PickleyConfig.base: Folder considered as base for pickley installs (same folder as pickley)
    ├── .pickley/                       # PickleyConfig.meta: Folder where pickley will manage installations
    │   ├── .cache/                     # PickleyConfig.cache: Internal cache folder, can be scrapped any time
    │   │   ├── tox.ping                # PackageSpec.ping_path: Ping file used to throttle auto-upgrade checks
    │   │   └── tox.latest              # Latest version as determined by querying pypi
    │   ├── audit.log                   # Activity is logged here
    │   ├── config.json                 # Optional configuration provided by user
    │   ├── tox.lock                    # Lock while installation is in progress
    │   ├── tox/                        # PackageSpec.meta_path: Folder where all installed venvs for given package are found
    │   │   ├── .manifest.json          # PackageSpec.manifest_path: Metadata on current installation
    │   │   └── tox-2.9.1/              # PackageSpec.install_path: Actual installation, as packaged by pickley
    │   │       └── .manifest.json      # Metadata on this installation (repeated)
    ├── pickley -> .pickley/pickley/... # pickley itself
    └── tox -> .pickley/tox/2.9.1/...   # PackageSpec.exe_path(): Produced exe, can be a symlink or a self-upgrading wrapper


Pickley's metadata files
========================

Pickley tracks what it installed via files in ``<base>/.pickley/`` via a number of files.


**<base>/.pickley/<name>/.manifest.json** contains info as to what is currently installed and
some info about when/how it was installed::

    {
        "entrypoints": {
          "tox": "tox:cmdline",
          "tox-quickstart": "tox._quickstart:main"
        },
        "install_info": {
            "args": "-P3.9 -dwrap install tox==1.2.3",
            "timestamp": "2020-11-01 12:51:01",
            "vpickley": "2.3.0"
        },
        "pinned": "1.2.3",
        "settings": {
            "delivery": "wrap",
            "index": "https://pypi-mirror.mycompany.net/pypi",
            "python": "/usr/bin/python3"
        },
        "version": "1.2.3"
    }


**<base>/.pickley/.cache/<name>.latest** contains info about which version was determined to be
the latest for a given package, this is used to put a cooldown on query pypi via the
``auto-upgrade`` command::

    {
        "index": "https://pypi-mirror.mycompany.net/pypi",
        "install_info": {
            "args": "auto-upgrade mgit",
            "timestamp": "2020-11-01 12:51:39",
            "vpickley": "2.3.0"
        },
        "source": "latest",
        "version": "1.2.1"
    }
