#!/usr/bin/env python3
"""Setup script for holo-accelerant."""
from setuptools import setup, find_packages

setup(
    name="holo-accelerant",
    version="0.1.0",
    description="Holo Accelerant - Python package installation at ludicrous speed.",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "accelero=accelero.cli.main:main",
        ],
    },
    install_requires=[
        "packaging>=23.0",
        "httpx>=0.25",
        "rich>=13.0",
    ],
    extras_require={
        "dev": ["build", "twine"],
        "test": ["pytest>=7.0"],
    },
)
