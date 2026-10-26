"""Declare the package metadata, runtime dependencies, and CLI entry point."""

from pathlib import Path

import setuptools

ROOT = Path(__file__).resolve().parent
install_requires = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

setuptools.setup(
    name="mender-richer-cli",
    version="0.1.1",
    description="A terminal UI and CLI for managing Mender devices.",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    package_dir={"": "src"},
    py_modules=["main", "api", "config", "display", "protocol", "remote", "terminal_io"],
    python_requires=">=3.10",
    install_requires=install_requires,
    extras_require={"test": ["pytest>=8", "pytest-cov>=5"]},
    entry_points={
        'console_scripts': [
            'mrc=main:main',
        ]
    },
    include_package_data=True,
)
