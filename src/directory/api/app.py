from fastapi import FastAPI

from directory.api import admin, mosques, times


def create_app() -> FastAPI:
    app = FastAPI(
        title="UK Mosque Jamaat Directory",
        version="0.1.0",
        description="Jamaat timetables for UK mosques. Data layer for Sirat.",
    )
    app.include_router(mosques.router, prefix="/v1")
    app.include_router(times.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1")
    return app


app = create_app()
