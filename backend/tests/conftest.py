import pytest

from app.config import Settings
from app.db import init_engine
from app.main import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLSWARM_ADMIN_TOKEN", "admin-token-test")
    monkeypatch.setenv("SOLSWARM_MONITOR_TOKEN", "monitor-token-test")
    monkeypatch.setenv("SOLSWARM_DATABASE_URL", f"sqlite:///{tmp_path / 'swarm.db'}")
    monkeypatch.setenv("SOLSWARM_AUTOSTART", "false")
    monkeypatch.setenv("SOLSWARM_MODE", "demo")
    monkeypatch.setenv("SOLSWARM_AGENT_TIMEOUT_SECONDS", "2")
    cfg = Settings()
    init_engine(cfg)
    return cfg


@pytest.fixture
def swarm(settings):
    app = create_app(settings)
    return app.state.swarm


@pytest.fixture
def api(settings):
    from fastapi.testclient import TestClient

    application = create_app(settings)
    with TestClient(application) as client:
        yield client
