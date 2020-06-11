
``.manifest.json``::

    {
        "entrypoints": {
          "tox": "tox:cmdline",
          "tox-quickstart": "tox._quickstart:main"
        },
        "pickley": {
            "command": "-P3.7 -dwrap install tox==1.2.3",
            "timestamp": "2020-05-24 12:57:05",
            "version": "1.9.19"
        },
        "pinned": "1.2.3",
        "settings": {
            "delivery": "wrap",
            "index": "https://pypi-mirror.mycompany.net/pypi",
            "python": "..."
        },
        "version": "3.14.6"
    }


``.cache/tox.latest``::

    {
        "index": "https://pypi-mirror.mycompany.net/pypi",
        "pickley": {
            "command": "auto-upgrade tox",
            "timestamp": "2020-05-24 12:57:05",
            "version": "1.9.19"
        },
        "source": "latest",
        "version": "1.2.3"
    }

