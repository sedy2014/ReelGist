import importlib
import os
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, os.path.abspath(BACKEND))


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """Point the database module at a throwaway file (never the real library)."""
    import database
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


@pytest.fixture()
def app_module(db_path):
    import app as app_mod
    importlib.reload(app_mod)  # fresh _inflight set; init_db already pointed at tmp db
    return app_mod


@pytest.fixture()
def client(app_module):
    app_module.app.testing = True
    return app_module.app.test_client()
