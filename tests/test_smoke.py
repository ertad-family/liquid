from importlib.metadata import version
from pathlib import Path

import liquid
from liquid import __version__


def test_version_matches_package_metadata():
    assert __version__ == version("liquid-api")


def test_py_typed_marker_present():
    """PEP 561: the py.typed marker must ship so type checkers trust our hints."""
    marker = Path(liquid.__file__).parent / "py.typed"
    assert marker.exists(), "py.typed marker missing — package would not be recognized as typed"
