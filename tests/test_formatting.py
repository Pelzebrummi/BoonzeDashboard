import pytest

from app.main import _de_input, _de_money, _de_number, classify_deviation, verdict_text


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        (2111.0, 3, "2.111"),
        (1234.56, 3, "1.234,56"),
        (0.38484, 4, "0,3848"),
        (12345678.0, 3, "12.345.678"),
        (-5.26, 1, "−5,3"),
        (0.0, 3, "0"),
    ],
)
def test_de_number_groups_thousands(value, digits, expected):
    assert _de_number(value, digits) == expected


def test_de_input_has_no_thousands_separator():
    # In a form field "1.234" would read as 1234 or 1,234; the input format avoids it.
    assert _de_input(1234.5) == "1234,5"
    assert _de_input(2111.0) == "2111"


@pytest.mark.parametrize(
    ("value", "expected"), [(812.4, "812,40"), (1012.4, "1.012,40"), (-187.6, "−187,60")]
)
def test_de_money_always_has_two_decimals(value, expected):
    assert _de_money(value) == expected


@pytest.mark.parametrize(
    ("deviation", "status"),
    [(0.0, "ok"), (-5.0, "ok"), (5.3, "warn"), (-15.0, "warn"), (15.1, "bad"), (-40.0, "bad")],
)
def test_classify_deviation(deviation, status):
    assert classify_deviation(deviation) == status


def test_verdict_text():
    assert verdict_text(-5.26) == "Home Assistant hat 5,3 % weniger gemessen als abgerechnet."
    assert verdict_text(2.0) == "Home Assistant hat 2 % mehr gemessen als abgerechnet."
    assert verdict_text(0.01) == "Messung und Rechnung stimmen überein."


def test_values_that_round_to_zero_have_no_minus():
    assert _de_number(-0.0001) == "0"
    assert _de_money(-0.001) == "0,00"


def test_de_input_keeps_cents_for_euro():
    assert _de_input(812.4, "EUR") == "812,40"
    assert _de_input(812.4, "kWh") == "812,4"


@pytest.mark.parametrize(("deviation", "status"), [(5.04, "ok"), (15.04, "warn"), (5.06, "warn")])
def test_status_follows_the_rounded_percentage(deviation, status):
    assert classify_deviation(deviation) == status
