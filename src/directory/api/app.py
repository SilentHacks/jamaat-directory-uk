from importlib import resources

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from directory.api import admin, mosques, times
from directory.web import routes as web_routes


def create_app() -> FastAPI:
    app = FastAPI(
        title="UK Mosque Jamaat Directory",
        version="0.1.0",
        description="Jamaat timetables for UK mosques. Data layer for Sirat.",
    )
    app.include_router(mosques.router, prefix="/v1")
    app.include_router(times.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1")
    app.include_router(web_routes.router)

    static_dir = resources.files("directory.web").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app


app = create_app()
