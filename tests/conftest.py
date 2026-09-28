"""Never let offline tests consume a user's managed credential or budget."""
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_HOME", str(tmp_path / "jev-state"))
    for name in ("TYPESAFE_API_KEY", "JEV_API_KEY", "JEV_OFFLINE_MODE", "JEV_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)
