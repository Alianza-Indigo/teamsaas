import importlib.util
from pathlib import Path
from fastapi.testclient import TestClient


def load_entrypoint():
    spec = importlib.util.spec_from_file_location('teamsaas_entry_for_test', Path(__file__).resolve().parents[1] / 'index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vercel_missing_secrets_returns_maintenance(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('FORM_SECRET', raising=False)
    module = load_entrypoint()
    assert not module.production_ready()
    with TestClient(module.app) as client:
        assert client.get('/ingresar').status_code == 503
        assert client.post('/ingresar').status_code == 503


def test_vercel_needs_postgres_and_persistent_key(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.setenv('FORM_SECRET', 'long-and-unique-secret-for-tests-not-production')
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///./test.db')
    assert not load_entrypoint().production_ready()
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test:pass@localhost/db')
    assert load_entrypoint().production_ready()
