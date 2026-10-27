from datetime import date

import pytest

from app.parsing import guess_medium, parse_german_date, parse_german_number, suggest_entries
from tests.helpers import SAMPLE_BILL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", 1234.56),
        ("1.234", 1234.0),
        ("12.345.678", 12345678.0),
        ("812,40", 812.40),
        ("2111", 2111.0),
        ("1234.5", 1234.5),
        ("-70,00", -70.0),
        ("1 234,5", 1234.5),
    ],
)
def test_parse_german_number(raw, expected):
    assert parse_german_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "1e5", "1_000", "", "12a", "9" * 400])
def test_parse_german_number_rejects_non_numbers(raw):
    with pytest.raises(ValueError):
        parse_german_number(raw)


def test_parse_german_date():
    assert parse_german_date("31.12.2025") == date(2025, 12, 31)
    assert parse_german_date("1.2.26") == date(2026, 2, 1)
    with pytest.raises(ValueError):
        parse_german_date("31.12.0202")


def test_guess_medium():
    assert guess_medium("Ihre Erdgasabrechnung, Verbrauch in kWh") == "gas"
    assert guess_medium("Trinkwasser und Abwasser") == "wasser"
    assert guess_medium("Jahresabrechnung Strom") == "strom"
    assert guess_medium("Kontoauszug") == "sonstiges"


def test_gas_bill_in_kwh_is_gas():
    text = (
        "Ihre Erdgasabrechnung\nZustandszahl 0,9636 Brennwert 11,2 kWh/m³\n"
        "Arbeitspreis 12.345 kWh\nCO2-Preis 12.345 kWh\nEnergiesteuer 12.345 kWh\nVerbrauch 1.143 m³"
    )
    assert guess_medium(text) == "gas"


def test_suggestions_from_sample_bill():
    template, suggestions = suggest_entries("\n".join(SAMPLE_BILL))

    assert template.name == "Allgemein"
    by_kind = {s.kind: s for s in suggestions}
    assert set(by_kind) == {"verbrauch", "betrag", "zaehlerstand"}

    consumption = by_kind["verbrauch"]
    assert (consumption.medium, consumption.value, consumption.unit) == ("strom", 2111.0, "kWh")
    assert (consumption.period_start, consumption.period_end) == (date(2025, 1, 1), date(2025, 12, 31))

    assert by_kind["betrag"].value == pytest.approx(812.40)
    assert by_kind["betrag"].unit == "EUR"

    reading = by_kind["zaehlerstand"]
    assert (reading.period_start, reading.period_end, reading.value) == (None, date(2025, 12, 31), 12345.0)


def test_values_in_another_column_are_found():
    gap = " " * 90
    text = f"Abrechnungszeitraum{gap}01.01.2025 - 31.12.2025\nGesamtverbrauch{gap}2.111 kWh"
    _, suggestions = suggest_entries(text)
    assert [(s.kind, s.value) for s in suggestions] == [("verbrauch", 2111.0)]


def test_meter_reading_does_not_cut_the_year():
    text = "Zählerstand am 31.12.2025 (abgelesen durch Kunde, Ablesegrund: Turnus) 12.345 kWh"
    _, suggestions = suggest_entries(text)
    assert [(s.period_end, s.value) for s in suggestions] == [(date(2025, 12, 31), 12345.0)]


def test_only_one_amount_is_preselected_as_cost():
    text = "\n".join(
        [
            "Abrechnungszeitraum 01.01.2025 - 31.12.2025",
            "Gesamtbetrag brutto 1.012,40 EUR",
            "abzüglich geleistete Abschlagszahlungen 1.200,00 EUR",
            "Rechnungsbetrag (Guthaben) -187,60 EUR",
        ]
    )
    _, suggestions = suggest_entries(text)
    amounts = {s.value: s for s in suggestions if s.kind == "betrag"}
    assert set(amounts) == {1012.40, 1200.00, -187.60}
    assert [value for value, s in amounts.items() if s.checked] == [1012.40]
    # Payments made during the period are dated, not spread over it.
    assert amounts[1200.00].period_start is None


def test_no_period_means_no_consumption_suggestion():
    _, suggestions = suggest_entries("Gesamtverbrauch 2.111 kWh")
    assert suggestions == []
