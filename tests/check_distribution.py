"""Validate built archives and release versions without importing application dependencies."""

import ast
from email.parser import Parser
import os
from pathlib import Path
import sys
import tarfile
import zipfile

from packaging.version import Version


def check_distributions(directory: Path, release_tag: str = '') -> str:
    """Check wheel modules, source test fixtures, and agreement between release versions.

    :param directory: Directory containing exactly one wheel and one source archive.
    :param release_tag: Optional published tag, with or without a leading v.
    :returns: The validated package version.
    :raises ValueError: An archive is incomplete or versions disagree.
    """
    wheels = list(directory.glob('*.whl'))
    sources = list(directory.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError('expected exactly one wheel and one source archive')
    expected_modules = {path.name for path in (Path(__file__).resolve().parents[1] / 'src').glob('*.py')}
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        if not expected_modules <= names:
            raise ValueError(f'wheel is missing application modules: {expected_modules - names}')
        metadata_name = next(name for name in names if name.endswith('.dist-info/METADATA'))
        metadata = Parser().parsestr(wheel.read(metadata_name).decode())
        version = metadata['Version']
        tree = ast.parse(wheel.read('main.py').decode())
        cli_version = next(ast.literal_eval(node.value) for node in tree.body
                           if isinstance(node, ast.Assign) and any(
                               isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
        if Version(version) != Version(cli_version):
            raise ValueError('package and CLI versions disagree')
        if release_tag and Version(release_tag.removeprefix('v')) != Version(version):
            raise ValueError(f'release tag {release_tag!r} does not match package version {version}')
    with tarfile.open(sources[0]) as source:
        names = {'/'.join(name.split('/')[1:]) for name in source.getnames()}
        required = {'setup.py', 'pyproject.toml', 'requirements.txt', 'README.md',
                    'tests/conftest.py', 'tests/test_protocol.py', 'tests/tui_smoke.py'}
        if not required <= names:
            raise ValueError(f'source archive is missing required files: {required - names}')
    return version


if __name__ == '__main__':
    version = check_distributions(Path(sys.argv[1]), os.environ.get('RELEASE_TAG', ''))
    print(f'Validated wheel, source archive, and version {version}.')
