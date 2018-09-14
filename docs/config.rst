Configuration
=============

**pickley** loads a configuration file (if present): ``<base>/.pickley/config.json``
(``<base>`` refers to the folder where **pickley** resides)

Other config files can be included, via an ``include`` directive, or per invocation via the ``--config`` command line argument.

Config files are json as their name implies, here's an example::

    {
      "index": "https://pypi-mirror.mycompany.net/pypi",
      "include": "foo.json",
      "bundle": {
        "dev": "tox twine"
      },
      "channel": {
        "stable": {
          "tox": "3.2.1",
          "twine": "1.11"
        }
      },
      "default": {
        "delivery": "wrap"
      },
      "select": {
        "delivery": {
          "symlink": "twine"
        }
      }
    }


The above means:

- Use a custom pypi index

- Include a secondary config file (relative paths are relative to config file stating the ``include``)

- Define a "bundle" called ``dev`` (convenience for installing multiple things at once via ``pickley install bundle:dev``)

- Define a channel called "stable" specifying which versions of which pypi packages should be used

- Use the ``wrap`` delivery mode by default (which will create a self-upgrading wrapper,
  checking for new versions and auto-installing them every time you invoke corresponding command)

- Customize delivery mode for ``twine`` (use regular ``symlink`` here in this case, instead of default auto-upgrading ``wrap``)


Layout
======

```
{
    "bundle": {
        "mybundle": "tox twine"
    },
    "channel": {
        "stable": {
            "tox": "1.0"
        }
    },
    "default": {
        "channel": "latest",
        "delivery": "wrap, or symlink, or copy",
        "packager": "venv"
    },
    "delivery": {
        "wrap": "logfetch mgit"
    },
    "include": [
        "~/foo/pickley.json"
    ],
    "index": "https://pypi.org/",
    "select": {
        "twine": {
            "channel": "latest",
            "delivery": "symlink",
            "packager": "pex",
        }
    }
}
```
