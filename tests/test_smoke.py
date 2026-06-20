import directory


def test_package_has_version():
    assert isinstance(directory.__version__, str)
    assert directory.__version__
