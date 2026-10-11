import videokit
import videokit.core
import videokit.tools


def test_version_is_exported() -> None:
    assert isinstance(videokit.__version__, str)
    assert videokit.__version__


def test_subpackages_import() -> None:
    assert videokit.core.__name__ == "videokit.core"
    assert videokit.tools.__name__ == "videokit.tools"
