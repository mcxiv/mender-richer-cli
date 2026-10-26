"""Declare the package metadata, runtime dependencies, and CLI entry point."""

from pathlib import Path

import setuptools

ROOT = Path(__file__).resolve().parent
install_requires = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

setuptools.setup(
    name="mender-richer-cli",
    version="0.1.1",
    package_dir={"": "src"},
    py_modules=["main", "api", "config", "display", "protocol", "remote"],
    python_requires=">=3.10",
    install_requires=install_requires,
    entry_points={
        'console_scripts': [
            'mrc=main:main',
        ]
    },
    include_package_data=True,
)
