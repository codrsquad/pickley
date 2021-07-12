"""
download_url: releases/download/v{version}/pickley
"""


from setuptools import setup


if __name__ == "__main__":
    setup(
        name="pickley",
        setup_requires="setupmeta",
        versioning="dev",
        author="Zoran Simic zoran@simicweb.com",
        url="https://github.com/codrsquad/pickley",
        python_requires='>=3.6',
        entry_points={
            "console_scripts": [
                "pickley = pickley.__main__:main",
            ],
        },
        classifiers=[
            "Development Status :: 4 - Beta",
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
            "Programming Language :: Python :: Implementation :: CPython",
            "Topic :: Software Development :: Build Tools",
            "Topic :: System :: Installation/Setup",
            "Topic :: System :: Software Distribution",
            "Topic :: Utilities",
        ],
    )
