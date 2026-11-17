from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.state import AppState
from tests.conftest import GROUPS, FakeProvider, FakeSerpApi, make_db, no_sleep

CENTER = (date.today() + timedelta(days=30)).isoformat()
WEEK = {"direction": "from_uk", "foreign": "KHI", "center_date": CENTER, "selected_group": "NCL"}


def build_client(env, **overrides):
    state = AppState.build(
        env,
        fast_flights=FakeProvider(),
        serpapi=FakeSerpApi(available=False),
        groups=GROUPS,
        sleep=no_sleep,
        **overrides,
    )
    client = TestClient(create_app(state, serve_frontend=False))
    client.state = state
    return client


@pytest.fixture(params=["sqlite", "turso"])
def client(env, request):
    """The API end to end, against both database backends."""
    with build_client(env, db=make_db(env.db_path, request.param)) as c:
        yield c


def run_all_jobs(client, params=WEEK, headers=None):
    """Do what the browser does: fetch /week, run each job via /fetch, until none are left."""
    statuses = []
    for _ in range(10):
        jobs = client.get("/api/week", params=params).json()["jobs"]
        if not jobs:
            return statuses
        for job in jobs:
            res = client.post("/api/fetch", json=job, headers=headers or {})
            assert res.status_code == 200
            statuses.append(res.json()["status"])
    raise AssertionError("jobs never finished")


def test_uk_groups(client):
    assert [g["id"] for g in client.get("/api/uk-groups").json()] == ["LON", "MAN", "NCL"]


def test_airports_autocomplete(client):
    res = client.get("/api/airports", params={"q": "kara"}).json()
    assert "KHI" in [a["iata"] for a in res]
    assert client.get("/api/airports", params={"q": "khi"}).json()[0]["iata"] == "KHI"


def test_week_flow(client):
    first = client.get("/api/week", params={**WEEK, "foreign": "khi"}).json()
    assert first["pending_count"] == len(first["jobs"]) == 21
    assert first["jobs"][0] == {
        "direction": "from_uk",
        "foreign": "KHI",
        "group": "NCL",
        "date": CENTER,
        "detail": True,
        "force": False,
        "route_days": 1,
    }
    assert len(first["dates"]) == 7 and len(first["groups"]) == 3
    assert all(d["loading"] for g in first["groups"] for d in g["days"])

    statuses = run_all_jobs(client)
    assert set(statuses) == {"done"}
    week = client.get("/api/week", params=WEEK).json()
    assert week["pending_count"] == 0 and week["jobs"] == []
    for g in week["groups"]:
        assert g["cheapest_price"] is not None
        assert all(d["status"] == "ok" and not d["loading"] for d in g["days"])
    assert week["min_price"] <= week["max_price"]

    flights = client.get(
        "/api/flights",
        params={"direction": "from_uk", "foreign": "KHI", "group": "LON", "date": CENTER},
    ).json()
    assert {f["origin_airport"] for f in flights["flights"]} == {"LHR", "LGW", "STN", "LTN"}
    assert flights["meta"]["fetched_at"] is not None
    assert flights["flights"][0]["fetched_at"] is not None

    wk = client.get(
        "/api/flights/week",
        params={"direction": "from_uk", "foreign": "KHI", "group": "NCL", "center_date": CENTER},
    ).json()
    assert len(wk["days"]) == 7


def test_fetch_validates_input(client):
    bad = {"direction": "from_uk", "foreign": "KHIX", "group": "NCL", "date": CENTER}
    assert client.post("/api/fetch", json=bad).status_code == 422
    unknown = {"direction": "from_uk", "foreign": "KHI", "group": "XXX", "date": CENTER}
    assert client.post("/api/fetch", json=unknown).json()["status"] == "error"


def test_unknown_group_404(client):
    res = client.get(
        "/api/flights",
        params={"direction": "from_uk", "foreign": "KHI", "group": "XXX", "date": CENTER},
    )
    assert res.status_code == 404


def test_settings_roundtrip_and_validation(client):
    assert client.get("/api/settings").json()["ttl_days"] == 2
    res = client.put("/api/settings", json={"ttl_days": 4, "delay_seconds": [0.1, 0.25]})
    assert res.status_code == 200
    # Cache expiry comes only from CACHE_TTL_DAYS; the request's ttl_days is ignored.
    assert res.json()["ttl_days"] == 2 and res.json()["delay_seconds"] == [0.1, 0.25]
    assert client.put("/api/settings", json={"delay_seconds": [0, 1]}).status_code == 422
    assert client.put("/api/settings", json={"delay_seconds": [2, 1]}).status_code == 422
    assert client.put("/api/settings", json={"provider_mode": "bogus"}).status_code == 422


def test_refresh_returns_force_jobs(client):
    day = client.post(
        "/api/refresh",
        json={
            "scope": "day",
            "direction": "from_uk",
            "foreign": "khi",
            "group": "NCL",
            "date": CENTER,
        },
    )
    [job] = day.json()["jobs"]
    assert job["force"] is True and job["foreign"] == "KHI" and job["detail"] is True
    assert (
        client.post(
            "/api/refresh", json={"scope": "day", "direction": "from_uk", "foreign": "KHI"}
        ).status_code
        == 422
    )
    week = client.post(
        "/api/refresh",
        json={
            "scope": "week",
            "direction": "from_uk",
            "foreign": "KHI",
            "center_date": CENTER,
            "group": "NCL",
        },
    ).json()["jobs"]
    assert len(week) == 21 and all(j["force"] for j in week)


def test_cache_controls(client):
    run_all_jobs(client)
    assert client.post("/api/cache/expire-all").json()["expired"] > 0
    week = client.get("/api/week", params=WEEK).json()
    assert any(d["is_stale"] for g in week["groups"] for d in g["days"])
    assert len(week["jobs"]) == 21  # expired data is re-fetched
    assert client.delete("/api/cache").json()["deleted"] > 0


def test_status(client):
    s = client.get("/api/status").json()
    assert s["active_provider"] == "fast_flights"
    assert s["serpapi_usage"] == {"used": 0, "cap": 230}
    assert s["database"] == "sqlite"


def test_default_delay_is_half_to_one_second(env, db):
    from app.cache import SettingsStore

    assert SettingsStore(db, env).get().delay_seconds == (0.5, 1.0)


def test_toggle_uk_groups(client):
    all_groups = client.get("/api/uk-groups", params={"include_disabled": True}).json()
    assert [g["enabled"] for g in all_groups] == [True, True, True]

    res = client.put("/api/uk-groups", json={"enabled": ["LON", "NCL"]})
    assert res.status_code == 200
    assert {g["id"]: g["enabled"] for g in res.json()} == {"LON": True, "MAN": False, "NCL": True}
    assert [g["id"] for g in client.get("/api/uk-groups").json()] == ["LON", "NCL"]

    week = client.get("/api/week", params=WEEK).json()
    assert [g["group_id"] for g in week["groups"]] == ["LON", "NCL"]
    assert all(j["group"] != "MAN" for j in week["jobs"])
    assert (
        client.get(
            "/api/flights",
            params={"direction": "from_uk", "foreign": "KHI", "group": "MAN", "date": CENTER},
        ).status_code
        == 404
    )

    assert client.put("/api/uk-groups", json={"enabled": []}).status_code == 422
    assert client.put("/api/uk-groups", json={"enabled": ["XXX"]}).status_code == 422


@pytest.fixture
def admin_client(env):
    import app.api.routes as routes

    routes.FAILED_LOGIN_DELAY_SECONDS = 0
    with build_client(env.model_copy(update={"admin_password": "s3cret"})) as c:
        yield c


def test_defaults_to_fast_flights_and_open_without_admin_password(client):
    assert client.get("/api/settings").json()["provider_mode"] == "fast_flights"
    assert client.get("/api/status").json()["admin_protected"] is False
    assert client.put("/api/settings", json={"provider_mode": "auto"}).status_code == 200
    assert client.post("/api/admin/check").status_code == 200


def test_admin_password_protects_shared_settings(admin_client):
    c = admin_client
    assert c.get("/api/status").json()["admin_protected"] is True
    protected = [
        ("put", "/api/settings", {"provider_mode": "auto"}),
        ("put", "/api/uk-groups", {"enabled": ["LON"]}),
        ("post", "/api/cache/expire-all", None),
        ("delete", "/api/cache", None),
        ("post", "/api/admin/check", None),
    ]
    for method, url, body in protected:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(c, method)(url, **kwargs).status_code == 401, url
        bad = {"X-Admin-Password": "nope"}
        assert getattr(c, method)(url, headers=bad, **kwargs).status_code == 401, url
        ok = {"X-Admin-Password": "s3cret"}
        assert getattr(c, method)(url, headers=ok, **kwargs).status_code == 200, url
    assert c.get("/api/settings").json()["provider_mode"] == "auto"


def test_public_endpoints_stay_open_with_admin_password(admin_client):
    c = admin_client
    assert c.get("/api/week", params=WEEK).status_code == 200
    assert c.get("/api/settings").status_code == 200
    assert set(run_all_jobs(c)) == {"done"}
    res = c.post(
        "/api/refresh",
        json={"scope": "week", "direction": "from_uk", "foreign": "KHI", "center_date": CENTER},
    )
    assert res.status_code == 200


def test_cache_ttl_comes_from_env_and_applies_on_startup(env):
    from app.cache import RouteKey, utcnow
    from app.models import Completeness, Direction

    state = AppState.build(
        env,
        fast_flights=FakeProvider(),
        serpapi=FakeSerpApi(available=False),
        groups=GROUPS,
        sleep=no_sleep,
    )
    key = RouteKey(Direction.FROM_UK, "NCL", "KHI", date.today() + timedelta(days=30))
    fetched = utcnow()
    state.cache.store_result(
        key, [], provider="fake", completeness=Completeness.FULL, ttl_days=2, now=fetched
    )
    state.db.close()

    with build_client(env.model_copy(update={"cache_ttl_days": 5})) as c:
        assert c.get("/api/settings").json()["ttl_days"] == 5
        assert c.state.cache.get(key).expires_at == fetched + timedelta(days=5)
