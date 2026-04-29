import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET", "test-jwt")
os.environ.setdefault("EA_SECRET", "test-ea-secret")
os.environ.setdefault("ADMIN_PIN", "123456")
os.environ.setdefault("DATABASE_URL", "sqlite://:memory:")
os.environ.setdefault("DISABLE_OANDA_POLLER", "1")
os.environ.setdefault("DISABLE_EA_WATCHDOG", "1")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture
def server():
    """Provide a fresh server module with a clean in-memory state."""
    import importlib

    import webhook_server

    importlib.reload(webhook_server)
    return webhook_server


@pytest.fixture
def client(server):
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def auth_token(server):
    return server.generate_jwt("test-user", "test-device")


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def ea_headers():
    return {"X-EA-Secret": os.environ["EA_SECRET"]}
