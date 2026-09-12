"""FastAPI application and Lambda entry point.

Startup work happens in the ``lifespan`` handler, not at import: importing this module must never
touch the database, the network or the filesystem, so scripts, type checkers and tests can import
it freely. The handler creates the schema on start and closes the pooled clients on shutdown.

The request middleware binds ``request_id`` into the logging context, so every line emitted while
handling that request carries it without being passed around, and reports the duration when the
response leaves.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from sentiment_prep import __version__
from sentiment_prep.api import deps
from sentiment_prep.api.routes import public_router, router
from sentiment_prep.api.schemas import ErrorResponse
from sentiment_prep.api.security import require_api_key
from sentiment_prep.config import get_settings
from sentiment_prep.errors import AppError
from sentiment_prep.history.db import get_engine
from sentiment_prep.logging_config import (
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
    get_request_id,
)

logger = get_logger(__name__)

DESCRIPTION = """
Prepare a text dataset for sentiment analysis and measure what each preprocessing step does.

Typical flow: **load** a dataset (X, Hugging Face, or CSV) → **preprocess** with an ordered list
of steps → inspect the impact report → **label** (Comprehend, manual review, or both) →
**export** as CSV/Excel/Parquet/Markdown or **save** to S3.

Every response carries an `X-Request-Id` header; quote it when reading logs. When `API_KEY` is
configured, every endpoint except `/health` requires an `X-API-Key` header. Run history and X
spend are available under the `account` tag.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the history schema on start; close pooled HTTP and AWS clients on shutdown."""
    get_engine()
    logger.info("app_ready", runtime=get_settings().runtime, version=__version__)
    yield
    deps.close_clients()


def create_app() -> FastAPI:
    """Build the application. Kept as a factory so tests can construct isolated instances."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.runtime == "lambda" or None)

    app = FastAPI(
        title="Sentiment Prep API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "dataset", "description": "Load and inspect raw data."},
            {
                "name": "preprocess",
                "description": "Toggleable NLP steps and their measured impact.",
            },
            {"name": "label", "description": "Comprehend labels, manual review, checkpoints."},
            {"name": "export", "description": "CSV, Excel, Parquet, Markdown report, S3 save."},
            {"name": "account", "description": "X spend and run history."},
            {"name": "system", "description": "Health and diagnostics."},
        ],
        responses={"4XX": {"model": ErrorResponse}, "5XX": {"model": ErrorResponse}},
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "Content-Disposition"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        clear_context()
        request_id = (
            request.headers.get("X-Request-Id") or _lambda_request_id(request) or uuid.uuid4().hex
        )
        bind_context(request_id=request_id, method=request.method, path=request.url.path)
        started = time.perf_counter()
        logger.info("request_started", client=request.client.host if request.client else None)
        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "request_crashed",
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                exc_info=True,
            )
            raise
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "request_finished",
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("request_rejected", error=exc.message, status=exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(error=exc.message, request_id=get_request_id()).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error="internal error", request_id=get_request_id()).model_dump(),
        )

    # Applied to the router, not to /health, so an uptime probe needs no secret.
    app.include_router(public_router)
    app.include_router(router, dependencies=[Depends(require_api_key)])
    return app


def _lambda_request_id(request: Request) -> str | None:
    context = request.scope.get("aws.context")
    return getattr(context, "aws_request_id", None) if context else None


app = create_app()
# "auto" runs the lifespan handler on cold start and at container shutdown, which is where the
# schema is created and the pooled clients are closed.
handler = Mangum(app, lifespan="auto")
