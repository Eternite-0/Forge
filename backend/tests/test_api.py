import hashlib
import hmac
import secrets

from fastapi.testclient import TestClient


def test_api_health_and_single_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "api.sqlite3"))
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    # Import after setting DB path so the app's composition root uses the isolated DB.
    import importlib

    from forge_api import main

    importlib.reload(main)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    session = client.get("/health").json()["session"]
    token = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
    client.headers.update({"Authorization": f"Bearer {token}"})
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["conversation_id"] == "main"
    conversation = client.get("/api/conversation")
    assert conversation.status_code == 200
    assert conversation.json()["conversation_id"] == "main"
