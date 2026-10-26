"""Ensure the package/release checks reject incomplete or mislabeled builds."""

import io
from pathlib import Path
import tarfile
import zipfile

import pytest

from check_distribution import check_distributions


@pytest.fixture
def distributions(tmp_path):
    files = {path.name: path.read_bytes() for path in (Path(__file__).resolve().parents[1] / 'src').glob('*.py')}
    files['main.py'] = b'__version__ = "0.1.1"\n'
    files['mender_richer_cli-0.1.1.dist-info/METADATA'] = b'Metadata-Version: 2.4\nName: mender-richer-cli\nVersion: 0.1.1\n'
    with zipfile.ZipFile(tmp_path / 'package.whl', 'w') as wheel:
        for name, content in files.items():
            wheel.writestr(name, content)
    with tarfile.open(tmp_path / 'package.tar.gz', 'w:gz') as source:
        for name in ['setup.py', 'pyproject.toml', 'requirements.txt', 'README.md',
                     'tests/conftest.py', 'tests/test_protocol.py', 'tests/tui_smoke.py']:
            data = b'fixture'
            info = tarfile.TarInfo('package/' + name)
            info.size = len(data)
            source.addfile(info, io.BytesIO(data))
    return tmp_path


@pytest.mark.parametrize('tag', ['', '0.1.1', 'v0.1.1'])
def test_valid_distributions(distributions, tag):
    assert check_distributions(distributions, tag) == '0.1.1'


def test_wrong_release_tag_is_rejected(distributions):
    with pytest.raises(ValueError, match='does not match'):
        check_distributions(distributions, 'v9.0.0')


def test_missing_distribution_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='exactly one'):
        check_distributions(tmp_path)


def test_missing_wheel_module_is_rejected(distributions):
    with zipfile.ZipFile(distributions / 'package.whl', 'w') as wheel:
        wheel.writestr('main.py', '__version__ = "0.1.1"')
    with pytest.raises(ValueError, match='application modules'):
        check_distributions(distributions)
