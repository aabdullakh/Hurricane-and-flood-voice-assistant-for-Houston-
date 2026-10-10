import os
import tempfile

# Must be set BEFORE the app is imported.
_tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    API_SHARED_SECRET="test-secret",
    PHONE_HASH_SALT="test-salt",
    DISABLE_POLLER="1",
    NWS_USER_AGENT="(tests, tests@example.com)",
    GEMINI_API_KEY="",
    ENABLE_BACKBOARD="0",
    ENABLE_ADMIN="1",  # replay tests use /admin; production leaves it off
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db import CallSummary, GaugeReading, Profile, SessionLocal, init_db  # noqa: E402
from app import security  # noqa: E402
from app.main import app  # noqa: E402
from app.sources import nws  # noqa: E402
from app.state import STATE  # noqa: E402

HEADERS = {"X-API-Key": "test-secret"}


@pytest.fixture()
def client():
    init_db()  # tables must exist before we clear them (the app also calls this on startup)
    with SessionLocal() as s:
        for model in (GaugeReading, Profile, CallSummary):
            s.execute(delete(model))
        s.commit()
    STATE.mode, STATE.replay_name, STATE.replay_index = "live", None, 0
    nws.clear_cache()
    security._failures.clear()
    with TestClient(app) as c:
        yield c
