from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.state import AppState
from tests.conftest import GROUPS, FakeProvider, FakeSerpApi, no_sleep

CENTER = (date.today() + timedelta(days=30)).isoformat()


@pytest.fixture
def client(env):
    state = AppState.build(
        env,
        fast_flights=FakeProvider(),
        serpapi=FakeSerpApi(available=False),
        groups=GROUPS,
        sleep=no_sleep,
    )
    app = create_app(state, serve_frontend=False)
    with TestClient(app) as c:
        c.state = state
        yield c


def wait_idle(client):
    import time

    for _ in range(300):
        if client.state.fetcher.queue_size == 0:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


def test_uk_groups(client):
    assert [g["id"] for g in client.get("/api/uk-groups").json()] == ["LON", "MAN", "NCL"]


def test_airports_autocomplete(client):
    res = client.get("/api/airports", params={"q": "kara"}).json()
    assert "KHI" in [a["iata"] for a in res]
    assert client.get("/api/airports", params={"q": "khi"}).json()[0]["iata"] == "KHI"


def test_week_flow(client):
    params = {
        "direction": "from_uk",
        "foreign": "khi",
        "center_date": CENTER,
        "selected_group": "NCL",
    }
    first = client.get("/api/week", params=params).json()
    assert first["pending_count"] > 0
    assert len(first["dates"]) == 7 and len(first["groups"]) == 3
    wait_idle(client)
    week = client.get("/api/week", params=params).json()
    assert week["pending_count"] == 0
    for g in week["groups"]:
        assert g["cheapest_price"] is not None
        assert all(d["status"] == "ok" for d in g["days"])
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
    assert res.json()["ttl_days"] == 4 and res.json()["delay_seconds"] == [0.1, 0.25]
    assert client.put("/api/settings", json={"delay_seconds": [0, 1]}).status_code == 422
    assert client.put("/api/settings", json={"delay_seconds": [2, 1]}).status_code == 422
    assert client.put("/api/settings", json={"ttl_days": 9}).status_code == 422
    assert client.put("/api/settings", json={"provider_mode": "bogus"}).status_code == 422


def test_cache_controls_and_refresh(client):
    params = {
        "direction": "from_uk",
        "foreign": "KHI",
        "center_date": CENTER,
        "selected_group": "NCL",
    }
    client.get("/api/week", params=params)
    wait_idle(client)
    assert client.post("/api/cache/expire-all").json()["expired"] > 0
    week = client.get("/api/week", params=params).json()
    assert any(d["is_stale"] for g in week["groups"] for d in g["days"])
    wait_idle(client)
    res = client.post(
        "/api/refresh",
        json={
            "scope": "day",
            "direction": "from_uk",
            "foreign": "KHI",
            "group": "NCL",
            "date": CENTER,
        },
    )
    assert res.json() == {"queued": 1}
    assert (
        client.post(
            "/api/refresh", json={"scope": "day", "direction": "from_uk", "foreign": "KHI"}
        ).status_code
        == 422
    )
    wait_idle(client)
    assert client.delete("/api/cache").json()["deleted"] > 0


def test_status(client):
    s = client.get("/api/status").json()
    assert s["active_provider"] == "fast_flights"
    assert s["serpapi_usage"] == {"used": 0, "cap": 230}


def test_default_delay_is_half_to_one_second(env):
    from app.cache import Database, SettingsStore

    assert SettingsStore(Database(env.db_path), env).get().delay_seconds == (0.5, 1.0)


def test_toggle_uk_groups(client):
    all_groups = client.get("/api/uk-groups", params={"include_disabled": True}).json()
    assert [g["enabled"] for g in all_groups] == [True, True, True]

    res = client.put("/api/uk-groups", json={"enabled": ["LON", "NCL"]})
    assert res.status_code == 200
    assert {g["id"]: g["enabled"] for g in res.json()} == {"LON": True, "MAN": False, "NCL": True}
    assert [g["id"] for g in client.get("/api/uk-groups").json()] == ["LON", "NCL"]

    params = {
        "direction": "from_uk",
        "foreign": "KHI",
        "center_date": CENTER,
        "selected_group": "NCL",
    }
    week = client.get("/api/week", params=params).json()
    assert [g["group_id"] for g in week["groups"]] == ["LON", "NCL"]
    assert all(k.group_id != "MAN" for k in client.state.fetcher.queued_keys())
    assert (
        client.get(
            "/api/flights",
            params={"direction": "from_uk", "foreign": "KHI", "group": "MAN", "date": CENTER},
        ).status_code
        == 404
    )

    assert client.put("/api/uk-groups", json={"enabled": []}).status_code == 422
    assert client.put("/api/uk-groups", json={"enabled": ["XXX"]}).status_code == 422
