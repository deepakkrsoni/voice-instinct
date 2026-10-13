from fastapi import FastAPI
from fastapi.testclient import TestClient

from guard import refuse_other_origins


def client():
    app = FastAPI()

    @app.post("/api/offer")
    async def offer():
        return {"ok": True}

    refuse_other_origins(app, port=7860)
    return TestClient(app)


def test_a_web_page_elsewhere_cannot_open_a_voice_session():
    # The errand browser renders untrusted pages; one could otherwise connect and answer its own question.
    assert client().post("/api/offer", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client().post("/api/offer", headers={"Origin": "http://localhost:4000"}).status_code == 403


def test_the_orb_the_playground_and_non_browser_callers_still_connect():
    c = client()
    assert c.post("/api/offer", headers={"Origin": "null"}).status_code == 200  # Electron's file:// page
    assert c.post("/api/offer", headers={"Origin": "http://localhost:7860"}).status_code == 200  # /client
    assert c.post("/api/offer", headers={"Origin": "http://127.0.0.1:7860"}).status_code == 200
    assert c.post("/api/offer").status_code == 200  # no Origin: hooks from the claude CLI
