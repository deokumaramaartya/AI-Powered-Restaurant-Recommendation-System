import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.store import RestaurantStore
from evals.fixtures.restaurants import FIXTURE_LOW_MAX, FIXTURE_MEDIUM_MAX, fixture_frame


def make_settings(**overrides) -> Settings:
    values = {
        "groq_api_key": "",
        "budget_low_max": FIXTURE_LOW_MAX,
        "budget_medium_max": FIXTURE_MEDIUM_MAX,
        "max_candidates": 25,
        "cors_origins": "http://localhost:5173",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_store(settings: Settings) -> RestaurantStore:
    return RestaurantStore(fixture_frame(), settings.budget_low_max, settings.budget_medium_max)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def store(settings) -> RestaurantStore:
    return make_store(settings)


@pytest.fixture
def client(settings, store):
    app = create_app(settings=settings, store=store)
    with TestClient(app) as test_client:
        yield test_client
