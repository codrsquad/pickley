#!/usr/bin/env python

"""
url: https://github.com/zsimic
download_url: releases/download/v{version}/pickley
"""


from setuptools import setup


setup(
    name="pickley",
    setup_requires="setupmeta",
    versioning="dev",
    author="Zoran Simic zoran@simicweb.com",
    entry_points={
        "console_scripts": [
            "pickley = pickley.cli:protected_main",
        ],
    },
)
