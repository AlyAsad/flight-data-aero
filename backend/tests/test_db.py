import httpx
import pytest

from app.db import DatabaseError, TursoDatabase, _decode, _encode


def test_value_encoding_round_trip():
    for value in (None, 0, 42, -7, 1.5, "text", ""):
        assert _decode(_encode(value)) == value
    assert _encode(True) == {"type": "integer", "value": "1"}


def test_libsql_url_becomes_https_pipeline():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "ok",
                        "response": {
                            "type": "execute",
                            "result": {
                                "cols": [{"name": "n"}],
                                "rows": [[{"type": "integer", "value": "3"}]],
                            },
                        },
                    },
                    {"type": "ok", "response": {"type": "close"}},
                ]
            },
        )

    db = TursoDatabase(
        "libsql://aero-me.turso.io",
        "tok",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert db.query("SELECT 3 AS n") == [{"n": 3}]
    assert seen["url"] == "https://aero-me.turso.io/v2/pipeline"


def test_statement_and_network_errors_raise_database_error():
    def sql_error(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"type": "error", "error": {"message": "no such table: nope"}},
                    {"type": "ok"},
                ]
            },
        )

    db = TursoDatabase(
        "https://x.turso.io", "t", client=httpx.Client(transport=httpx.MockTransport(sql_error))
    )
    with pytest.raises(DatabaseError, match="no such table"):
        db.query("SELECT * FROM nope")

    unauthorized = TursoDatabase(
        "https://x.turso.io",
        "bad",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "nope"}))
        ),
    )
    with pytest.raises(DatabaseError):
        unauthorized.execute("DELETE FROM settings")


def test_usage_cap_is_atomic_on_both_backends(db):
    from app.cache import UsageStore

    usage = UsageStore(db)
    assert [usage.try_reserve(2) for _ in range(3)] == [True, True, False]
    assert usage.used() == 2
