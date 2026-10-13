"""Route the desktop orb reads at start-up, so config.toml stays the single source of truth."""

from fastapi import APIRouter


def make_gui_router(cfg) -> APIRouter:
    router = APIRouter()

    @router.get("/gui-config")
    async def gui_config():
        return {"name": cfg.name, "size": cfg.gui_size, "position": cfg.gui_position,
                "awake_timeout": cfg.awake_timeout}

    return router
