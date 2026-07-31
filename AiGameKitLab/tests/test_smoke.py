import pytest


def test_import_cli():
    pytest.importorskip("yaml")
    from aigamekit_lab.cli import main

    assert callable(main)


def test_version():
    from aigamekit_lab import __version__

    assert __version__
