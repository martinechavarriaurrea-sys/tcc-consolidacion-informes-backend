from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, dashboard, guias, health, reports, shipments, sistema, tracking
from app.core.config import get_settings
from app.core.exceptions import AppError, app_error_handler, generic_error_handler
from app.core.logging import configure_logging
from app.jobs.scheduler import start_scheduler, stop_scheduler

settings = get_settings()
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_started = False
    if not settings.disable_scheduler:
        await start_scheduler()
        scheduler_started = True
    yield
    if scheduler_started:
        await stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(
        title="TCC Consolidación Informes — ASTECO",
        description=(
            "Sistema interno para seguimiento, consolidación y reporte de guías TCC. "
            "Consulta estado en tiempo real, historial de eventos, alertas de inactividad "
            "e informes diarios/semanales."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    origins = ["*"]
    if settings.cors_origins and settings.cors_origins.strip() != "*":
        origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)

    prefix = "/api/v1"
    app.include_router(health.router)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(guias.router, prefix=prefix)
    app.include_router(sistema.router, prefix=prefix)
    app.include_router(shipments.router, prefix=prefix)
    app.include_router(tracking.router, prefix=prefix)
    app.include_router(dashboard.router, prefix=prefix)
    app.include_router(reports.router, prefix=prefix)

    return app


app = create_app()
