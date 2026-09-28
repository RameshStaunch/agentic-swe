import pytest
from fastapi.testclient import TestClient

from notes.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_DB", str(tmp_path / "notes.db"))
    return TestClient(app)


def test_create_and_get(client):
    created = client.post("/notes", json={"title": "a", "body": "b", "tags": ["x"]}).json()
    assert client.get(f"/notes/{created['id']}").json()["tags"] == ["x"]


def test_list_filters_by_tag(client):
    client.post("/notes", json={"title": "a", "body": "b", "tags": ["x"]})
    client.post("/notes", json={"title": "c", "body": "d", "tags": ["y"]})
    assert [n["title"] for n in client.get("/notes", params={"tag": "y"}).json()] == ["c"]


def test_delete(client):
    created = client.post("/notes", json={"title": "a", "body": "b"}).json()
    assert client.delete(f"/notes/{created['id']}").status_code == 204
    assert client.get(f"/notes/{created['id']}").status_code == 404
