import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import MAX_TOP_K, Settings, get_settings
from app.models.recommendation import MAX_CUISINES, MAX_EXTRA_PREFERENCES
from app.services.llm_groq import GroqLLMClient, LLMClient
from app.services.recommender import UNKNOWN_VALUE, ResponseCache
from app.services.store import RestaurantStore, load_store

logger = logging.getLogger(__name__)
request_logger = logging.getLogger("app.requests")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Shown next to the matching form field; the raw Pydantic wording is written for developers.
FIELD_MESSAGES = {
    "location": "Choose a locality from the list.",
    "budget": "Budget must be low, medium, or high.",
    "cuisines": f"Cuisines must be a list of at most {MAX_CUISINES} names.",
    "min_rating": "Minimum rating must be a number from 0 to 5.",
    "extra_preferences": f"Extra preferences must be text of at most {MAX_EXTRA_PREFERENCES} characters.",
    "top_k": f"Number of results must be a whole number from 1 to {MAX_TOP_K}.",
}


def configure_logging(level_name: str) -> None:
    level = logging.getLevelName(level_name.strip().upper())
    if not logging.getLogger().handlers:
        logging.basicConfig(format=LOG_FORMAT)
    logging.getLogger("app").setLevel(level if isinstance(level, int) else logging.INFO)
    if not isinstance(level, int):
        logger.warning("LOG_LEVEL %r is not a logging level; using INFO.", level_name)


def _field_name(loc: list) -> str:
    if len(loc) > 1 and loc[0] == "body" and isinstance(loc[1], str):
        return loc[1]
    return "body"


def _readable(error: dict, field: str) -> str:
    if error.get("type") == UNKNOWN_VALUE or field not in FIELD_MESSAGES:
        return str(error.get("msg", "")).removeprefix("Value error, ")
    return FIELD_MESSAGES[field]


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # The rejected input is not echoed back: NaN or Infinity cannot be serialized as JSON.
    errors = []
    seen = set()
    for error in exc.errors():
        loc = list(error.get("loc", ()))
        field = _field_name(loc)
        message = _readable(error, field)
        if (field, message) in seen:
            continue
        seen.add((field, message))
        errors.append({"loc": loc, "field": field, "msg": message, "type": str(error.get("type", ""))})
    return JSONResponse(status_code=422, content={"detail": errors})


async def _log_request(request: Request, call_next):
    # Always server-generated: a client-supplied id is never trusted (SEC-08).
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    request.state.log_fields = {}
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        fields = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            **request.state.log_fields,
        }
        request_logger.info(json.dumps(fields, ensure_ascii=False))


def create_app(
    settings: Settings | None = None, store: RestaurantStore | None = None, llm: LLMClient | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if store is not None:
            app.state.store = store
        else:
            app.state.store = load_store(settings.data_path, settings.budget_low_max, settings.budget_medium_max)
        if llm is not None:
            app.state.llm = llm
        elif settings.groq_api_key:
            app.state.llm = GroqLLMClient.from_settings(settings)
        else:
            logger.warning("GROQ_API_KEY is not set; every request will use the fallback ranker.")
        yield

    app = FastAPI(title="AI Restaurant Recommendation API", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = None
    app.state.llm = None
    app.state.cache = ResponseCache()
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.middleware("http")(_log_request)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router, prefix="/api")
    return app


app = create_app()
