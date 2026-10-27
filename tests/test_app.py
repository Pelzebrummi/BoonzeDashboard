from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import INGRESS
from tests.helpers import SAMPLE_BILL, fake_home_assistant, make_pdf

BASE = INGRESS["X-Ingress-Path"]


def upload(client, content: bytes, name: str = "rechnung.pdf"):
    return client.post("/upload", files={"file": (name, content, "application/pdf")})


def save_rows(client, document_id: int, rows: list[dict]):
    form = {}
    for index, row in enumerate(rows):
        form[f"rows-{index}-use"] = "on"
        form.update({f"rows-{index}-{key}": value for key, value in row.items()})
    return client.post(f"/documents/{document_id}/entries", data=form)


CONSUMPTION = {
    "kind": "verbrauch",
    "medium": "strom",
    "period_start": "2025-01-01",
    "period_end": "2025-12-31",
    "value": "2111",
    "unit": "kWh",
    "note": "",
}


def test_healthz(client):
    assert client.get("/healthz").text == "ok"


def test_links_use_the_ingress_prefix(client):
    page = client.get("/")
    assert page.status_code == 200
    assert f'href="{BASE}/upload"' in page.text


def test_requests_from_outside_ingress_are_rejected(settings):
    locked = replace(settings, allowed_clients=frozenset({"172.30.32.2"}))
    with TestClient(create_app(locked)) as client:
        assert client.get("/").status_code == 403


def test_upload_review_and_save(client):
    response = upload(client, make_pdf(SAMPLE_BILL))
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"{BASE}/documents/")
    document_id = int(location.rsplit("/", 1)[1])

    page = client.get(f"/documents/{document_id}")
    assert page.status_code == 200
    assert "Gesamtverbrauch" in page.text
    # Unticked empty rows must not block submitting the form in the browser.
    assert "required" not in page.text
    assert 'value="2111"' in page.text
    assert 'value="812,4"' in page.text

    response = save_rows(client, document_id, [CONSUMPTION])
    assert response.status_code == 303
    # After saving, nothing is pre-selected any more, so a second click cannot duplicate entries.
    assert "checked" not in client.get(f"/documents/{document_id}").text

    overview = client.get("/")
    assert "01.01.2025 – 31.12.2025" in overview.text
    assert "rechnung.pdf" in overview.text

    pdf = client.get(f"/documents/{document_id}/pdf")
    assert pdf.headers["content-type"] == "application/pdf"


def test_duplicate_upload_points_to_existing_document(client):
    first = upload(client, make_pdf(SAMPLE_BILL)).headers["location"]
    second = upload(client, make_pdf(SAMPLE_BILL)).headers["location"]
    assert second == f"{first}?duplicate=1"


def test_non_pdf_is_rejected(client):
    response = upload(client, b"hello", name="notiz.pdf")
    assert response.status_code == 400
    assert "keine PDF" in response.text


def test_broken_pdf_is_reported(client):
    response = upload(client, b"%PDF-1.4\nkaputt")
    assert response.status_code == 422


def test_oversized_upload_is_rejected(settings):
    small = replace(settings, max_upload_bytes=1024)
    with TestClient(create_app(small), headers=INGRESS) as client:
        response = upload(client, b"%PDF-" + b"0" * 4096)
        assert response.status_code == 413
    assert not list(small.documents_dir.glob("*"))


def test_consumption_needs_a_period(client):
    document_id = int(upload(client, make_pdf(SAMPLE_BILL)).headers["location"].rsplit("/", 1)[1])
    response = save_rows(client, document_id, [{**CONSUMPTION, "period_start": ""}])
    assert response.status_code == 400
    assert "Zeitraum" in response.text


def test_delete_entry_only_redirects_inside_the_app(client):
    client.post("/entries", data={**CONSUMPTION})
    response = client.post("/entries/1/delete", data={"next": "//evil.example"})
    assert response.headers["location"] == f"{BASE}/"


def ha_statistics(covered: bool = True):
    def respond(command):
        match command["type"]:
            case "recorder/get_statistics_metadata":
                return [{"statistic_id": "sensor.strom", "unit_class": "energy", "statistics_unit_of_measurement": "Wh"}]
            case "recorder/statistic_during_period":
                return {"change": 2000.0}
            case "recorder/statistics_during_period":
                return {"sensor.strom": [{"sum": 1.0}]} if covered else {}
        raise AssertionError(command)

    return respond


def test_overview_compares_with_home_assistant(settings):
    with fake_home_assistant(ha_statistics()) as (url, received):
        connected = replace(settings, ha_ws_url=url, ha_token="secret", statistic_ids={"strom": "sensor.strom"})
        with TestClient(create_app(connected), headers=INGRESS, follow_redirects=False) as client:
            client.post("/entries", data={**CONSUMPTION})
            client.post(
                "/entries",
                data={**CONSUMPTION, "kind": "betrag", "value": "812,40", "unit": "EUR"},
            )
            page = client.get("/")

    assert "2000 kWh" in page.text
    assert "-5,3 %" in page.text
    assert "812,40 €" in page.text
    [query] = [c for c in received if c["type"] == "recorder/statistic_during_period"]
    assert query["statistic_id"] == "sensor.strom"
    assert query["fixed_period"] == {
        "start_time": "2025-01-01T00:00:00+01:00",
        "end_time": "2026-01-01T00:00:00+01:00",
    }


def test_no_deviation_when_ha_data_starts_inside_the_period(settings):
    with fake_home_assistant(ha_statistics(covered=False)) as (url, _):
        connected = replace(settings, ha_ws_url=url, ha_token="secret", statistic_ids={"strom": "sensor.strom"})
        with TestClient(create_app(connected), headers=INGRESS) as client:
            client.post("/entries", data={**CONSUMPTION})
            page = client.get("/")
    assert "Wert unvollständig" in page.text
    assert " %</span>" not in page.text


def test_invalid_numbers_are_rejected(client):
    for value in ("nan", "inf", "1e5"):
        response = client.post("/entries", data={**CONSUMPTION, "value": value})
        assert response.status_code == 400


def test_review_form_saves_all_rows_or_none(client):
    document_id = int(upload(client, make_pdf(SAMPLE_BILL)).headers["location"].rsplit("/", 1)[1])
    response = save_rows(client, document_id, [CONSUMPTION, {**CONSUMPTION, "value": "kaputt"}])
    assert response.status_code == 400
    assert "01.01.2025 – 31.12.2025" not in client.get("/").text


def test_malformed_requests_show_the_error_page(client):
    response = client.get("/documents/abc")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Ungültige Anfrage" in response.text


def test_wrong_token_shows_an_error_instead_of_crashing(settings):
    with fake_home_assistant(lambda command: None) as (url, _):
        connected = replace(settings, ha_ws_url=url, ha_token="falsch", statistic_ids={"strom": "sensor.strom"})
        with TestClient(create_app(connected), headers=INGRESS) as client:
            client.post("/entries", data={**CONSUMPTION})
            page = client.get("/")
    assert page.status_code == 200
    assert "Anmeldung bei Home Assistant fehlgeschlagen" in page.text
