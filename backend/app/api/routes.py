from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.models.meta import HealthResponse, MetaResponse
from app.models.recommendation import RecommendationRequest, RecommendationResponse
from app.services.llm_groq import LLMClient
from app.services.recommender import InvalidPreferences, ResponseCache, log_fields, recommend
from app.services.store import DATA_NOT_LOADED, RestaurantStore

router = APIRouter()

_UNAVAILABLE = {503: {"description": DATA_NOT_LOADED}}


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> RestaurantStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail=DATA_NOT_LOADED)
    return store


def get_llm_client(request: Request) -> LLMClient | None:
    return getattr(request.app.state, "llm", None)


def get_cache(request: Request) -> ResponseCache | None:
    return getattr(request.app.state, "cache", None)


@router.get("/health", response_model=HealthResponse, responses=_UNAVAILABLE)
def health(request: Request):
    store = getattr(request.app.state, "store", None)
    if store is None:
        return JSONResponse(status_code=503, content={"status": "unavailable", "detail": DATA_NOT_LOADED})
    return HealthResponse(status="ok", restaurants_loaded=store.count)


@router.get("/meta", response_model=MetaResponse, response_model_exclude_none=True, responses=_UNAVAILABLE)
def meta(store: RestaurantStore = Depends(get_store)):
    return store.meta()


@router.post("/recommendations", response_model=RecommendationResponse, responses=_UNAVAILABLE)
def recommendations(
    body: RecommendationRequest,
    request: Request,
    store: RestaurantStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    llm: LLMClient | None = Depends(get_llm_client),
    cache: ResponseCache | None = Depends(get_cache),
):
    try:
        response, details = recommend(body, store, settings, llm=llm, cache=cache, trace=True)
    except InvalidPreferences as exc:
        raise RequestValidationError(exc.errors) from exc
    request.state.log_fields.update(log_fields(body, response, details))
    return response
