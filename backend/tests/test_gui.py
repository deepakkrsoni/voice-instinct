from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import Config
from gui import make_gui_router


def test_gui_config_serves_what_the_orb_needs_from_config():
    app = FastAPI()
    app.include_router(make_gui_router(Config(name="Juno", gui_size=200, gui_position="top-left", awake_timeout=12)))
    r = TestClient(app).get("/gui-config")
    assert r.status_code == 200
    assert r.json() == {"name": "Juno", "size": 200, "position": "top-left", "awake_timeout": 12}
