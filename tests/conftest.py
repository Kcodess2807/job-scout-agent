import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sdk(monkeypatch):
    """The tests run on fakes. The platform SDK is installed in this venv for `gravity run`; here it is out of the
    way, so memory collections are the in-process dict and no call can reach a gateway."""
    import store
    import tooling
    monkeypatch.setattr(tooling, "gravity", None)
    monkeypatch.setattr(store, "gravity", None)
