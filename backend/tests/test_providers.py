import json
from datetime import datetime

import pytest

from app.providers.base import ProviderError, TooManyAirportsError
from app.providers.fast_flights import parse_html
from app.providers.serpapi import parse_baggage, parse_response
from tests.conftest import FIXTURES


class TestFastFlights:
    def test_maps_fixture(self):
        flights = parse_html((FIXTURES / "fast_flights_ncl_khi.html").read_text())
        assert len(flights) == 3
        ek = next(f for f in flights if f.airlines == ["Emirates"])
        assert ek.origin_airport == "NCL" and ek.destination_airport == "KHI"
        assert ek.departure_local == datetime(2026, 12, 31, 14, 30)
        assert ek.arrival_local == datetime(2027, 1, 1, 6, 40)
        assert ek.arrival_day_offset == 1
        assert ek.stops == 1
        assert ek.layovers[0].airport == "DXB"
        assert ek.layovers[0].duration_minutes == 150
        assert ek.layovers[0].overnight is False
        assert ek.duration_minutes == 405 + 150 + 115
        assert ek.aircraft == ["Boeing 777", "Airbus A380"]
        assert ek.price_gbp == 512
        # Not provided by fast-flights:
        assert ek.flight_numbers is None and ek.baggage is None and ek.is_self_transfer is None

    def test_overnight_layover_and_leading_zero_hour(self):
        flights = parse_html((FIXTURES / "fast_flights_ncl_khi.html").read_text())
        qr = next(f for f in flights if f.airlines == ["Qatar Airways"])
        assert qr.departure_local == datetime(2026, 12, 31, 0, 20)
        assert qr.layovers[0].overnight is True

    def test_zero_price_is_unavailable(self):
        flights = parse_html((FIXTURES / "fast_flights_ncl_khi.html").read_text())
        tk = next(f for f in flights if f.airlines == ["Turkish Airlines"])
        assert tk.price_gbp is None

    def test_consent_page_is_an_error(self):
        with pytest.raises(ProviderError, match="consent"):
            parse_html((FIXTURES / "fast_flights_consent.html").read_text())

    def test_garbage_is_an_error(self):
        with pytest.raises(ProviderError):
            parse_html("<html><body>nope</body></html>")

    def test_ids_are_stable(self):
        html = (FIXTURES / "fast_flights_ncl_khi.html").read_text()
        assert [f.id for f in parse_html(html)] == [f.id for f in parse_html(html)]


class TestSerpApi:
    def load(self):
        return json.loads((FIXTURES / "serpapi_lon_khi.json").read_text())

    def test_combines_best_and_other(self):
        flights = parse_response(self.load())
        assert len(flights) == 4
        assert {f.origin_airport for f in flights} == {"LHR", "LGW", "STN"}

    def test_direct_flight_details(self):
        pk = next(f for f in parse_response(self.load()) if f.flight_numbers == ["PK 786"])
        assert pk.stops == 0
        assert pk.arrival_day_offset == 1
        assert pk.duration_minutes == 455
        assert pk.baggage.carry_on == "included"
        assert pk.baggage.checked == "fee"

    def test_layovers_self_transfer_and_airport_change(self):
        st = next(f for f in parse_response(self.load()) if f.origin_airport == "STN")
        assert st.is_self_transfer is True
        lay = st.layovers[0]
        assert lay.airport == "SAW" and lay.airport_change is True
        assert lay.overnight is True and lay.duration_minutes == 1195
        assert st.baggage.carry_on == "fee"

    def test_missing_price_is_null(self):
        ek = next(f for f in parse_response(self.load()) if f.airlines == ["Emirates"])
        assert ek.price_gbp is None
        assert ek.flight_numbers == ["EK 2", "EK 600"]

    def test_no_results_is_empty(self):
        data = json.loads((FIXTURES / "serpapi_no_results.json").read_text())
        assert parse_response(data) == []

    def test_errors(self):
        with pytest.raises(TooManyAirportsError):
            parse_response({"error": "Too many airports in departure_id: maximum is 7"})
        with pytest.raises(ProviderError):
            parse_response({"error": "Invalid API key."})

    def test_baggage_parsing(self):
        assert parse_baggage(["Average legroom"]) is None
        b = parse_baggage(["1st checked bag included", "Carry-on bag for a fee"])
        assert (b.checked, b.carry_on) == ("included", "fee")
