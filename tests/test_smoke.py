from importlib.metadata import version

from liquid import __version__


def test_version_matches_package_metadata():
    assert __version__ == version("liquid-api")
