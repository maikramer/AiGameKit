import pytest


def test_import_cli():
    pytest.importorskip("yaml")
    from gamedev_lab.cli import main

    assert callable(main)


def test_version():
    from gamedev_lab import __version__

    assert __version__
