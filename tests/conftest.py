import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

INGRESS = {"X-Ingress-Path": "/api/hassio_ingress/abc123", "X-Remote-User-Name": "chris"}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, allowed_clients=frozenset())


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers=INGRESS, follow_redirects=False) as client:
        yield client
