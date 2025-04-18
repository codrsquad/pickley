from setuptools import setup

setup(
    name="pickley",
    setup_requires="setupmeta",
    versioning="dev",
    author="Zoran Simic zoran@simicweb.com",
    url="https://github.com/codrsquad/pickley",
    python_requires=">=3.6",
    entry_points={
        "console_scripts": [
            "pickley = pickley.__main__:main",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: POSIX",
        "Operating System :: Unix",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: Implementation :: CPython",
        "Topic :: Software Development :: Build Tools",
        "Topic :: System :: Installation/Setup",
        "Topic :: System :: Software Distribution",
        "Topic :: Utilities",
    ],
    project_urls={
        "Documentation": "https://github.com/codrsquad/pickley/wiki",
        "Release notes": "https://github.com/codrsquad/pickley/wiki/Release-notes",
        "Source": "https://github.com/codrsquad/pickley",
    },
)
