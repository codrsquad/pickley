File layout
===========

File structure at ``<base>`` installation folder, example ``~/.local/bin``::

    tree <base>                         # PickleyConfig.base: Folder considered as base for pickley installs (same folder as pickley)
    ├── .pickley/                       # PickleyConfig.meta: Folder where pickley will manage installations
    │   ├── .cache/                     # PickleyConfig.cache: Internal cache folder, can be scrapped any time
    │   │   ├── tox.ping                # PackageSpec.ping_path: Ping file used to throttle auto-upgrade checks
    │   │   └── tox.latest              # Latest version as determined by querying pypi
    │   ├── audit.log                   # Activity is logged here
    │   ├── config.json                 # Optional configuration provided by user
    │   ├── tox.lock                    # Lock while installation is in progress
    │   ├── tox/                        # PackageSpec.meta_path: Folder where all installed venvs for given package are found
    │   │   ├── .manifest.json          # PackageSpec.manifest_path: Metadata on current installation
    │   │   └── tox-2.9.1/              # PackageSpec.install_path: Actual installation, as packaged by pickley
    │   │       └── .manifest.json      # Metadata on this installation
    ├── pickley -> .pickley/pickley/... # pickley itself
    └── tox -> .pickley/tox/2.9.1/...   # PackageSpec.exe_path(): Produced exe, can be a symlink or a self-upgrading wrapper

