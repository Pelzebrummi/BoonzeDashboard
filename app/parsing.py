"""Turn the text of a bill into suggested entries.

The suggestions only pre-fill the review form; nothing is stored until a person
has checked them. Provider-specific layouts go into PROVIDER_TEMPLATES: a
template whose ``detect`` pattern matches the text replaces the generic
patterns for the fields it defines.
"""

import math
import re
from dataclasses import dataclass, replace
from datetime import date

# The lookarounds keep a match from starting or ending inside a longer number,
# e.g. reading "31.12.20" out of "31.12.2025".
NUMBER = r"(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)(?!\d)"
MONEY = r"-?(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?!\d)"
DATE = r"(?<![\d.])\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})(?!\d)"
PERIOD = rf"({DATE})\s*(?:-|–|bis)\s*({DATE})"
UNIT = r"(kWh|m³|m3|cbm)"


@dataclass(frozen=True)
class Suggestion:
    kind: str
    medium: str
    period_start: date | None
    period_end: date
    value: float
    unit: str
    source: str
    checked: bool = True


@dataclass(frozen=True)
class Template:
    name: str
    detect: re.Pattern | None = None
    period: re.Pattern | None = None
    consumption: re.Pattern | None = None
    meter_reading: re.Pattern | None = None
    total: re.Pattern | None = None
    debit: re.Pattern | None = None
    medium: str | None = None


GENERIC = Template(
    name="Allgemein",
    period=re.compile(rf"(?:Abrechnungs|Liefer|Verbrauchs)?zeitraum\D{{0,40}}{PERIOD}", re.IGNORECASE),
    consumption=re.compile(rf"(?:Gesamt)?verbrauch[^\n\d]{{0,60}}?({NUMBER})\s*{UNIT}\b", re.IGNORECASE),
    meter_reading=re.compile(
        rf"(?:Zählerstand|Ablesung)[^\n]{{0,80}}?({DATE})[^\n\d]{{0,80}}({NUMBER})(?:\s*{UNIT})?",
        re.IGNORECASE,
    ),
    # The bill's cost for the period; group 1 is the label, group 2 the amount.
    total=re.compile(rf"(Gesamtbetrag|Rechnungsbetrag)[^\n\d-]{{0,60}}?({MONEY})\s*(?:€|EUR)", re.IGNORECASE),
    # Payments made during the period (instalments, direct debits).
    debit=re.compile(
        rf"(Abschlag\w*|Abbuchung|Lastschrift)[^\n\d-]{{0,60}}?({MONEY})\s*(?:€|EUR)", re.IGNORECASE
    ),
)

# Example for a provider with its own layout:
# Template(
#     name="Stadtwerke Musterstadt",
#     detect=re.compile(r"Stadtwerke Musterstadt", re.IGNORECASE),
#     consumption=re.compile(rf"Arbeitspreis\s+({NUMBER})\s*{UNIT}"),
#     medium="strom",
# )
PROVIDER_TEMPLATES: list[Template] = []

# Words that identify a medium; the first group in each tuple counts triple.
MEDIUM_WORDS = {
    "gas": (("erdgas", "gasabrechnung", "gaszähler", "brennwert", "zustandszahl"), (r"\bgas",)),
    "strom": (("stromabrechnung", "stromzähler", "stromlieferung"), (r"strom",)),
    "wasser": (("trinkwasser", "abwasser", "wasserzähler"), (r"wasser",)),
}


def parse_german_number(raw: str) -> float:
    """Parse '1.234,56' -> 1234.56, '1.234' -> 1234.0 (dot as thousands separator), '1234.5' -> 1234.5."""
    text = raw.strip().replace(" ", "").replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+,\d+", text):
        normalized = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        normalized = text
    else:
        raise ValueError(f"not a number: {raw!r}")
    value = float(normalized)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {raw!r}")
    return value


def parse_german_date(raw: str) -> date:
    day, month, year = (int(part) for part in raw.split("."))
    if year < 100:
        year += 2000
    if not 1990 <= year <= 2100:
        raise ValueError(f"implausible year: {raw!r}")
    return date(year, month, day)


def normalize_unit(raw: str) -> str:
    return "m³" if raw.lower() in ("m3", "m³", "cbm") else raw


def guess_medium(text: str) -> str:
    lowered = text.lower()
    scores = {
        medium: 3 * sum(lowered.count(word) for word in strong) + sum(len(re.findall(p, lowered)) for p in weak)
        for medium, (strong, weak) in MEDIUM_WORDS.items()
    }
    medium, score = max(scores.items(), key=lambda item: item[1])
    return medium if score else "sonstiges"


def select_template(text: str) -> Template:
    for template in PROVIDER_TEMPLATES:
        if template.detect and template.detect.search(text):
            return replace(
                GENERIC,
                **{name: value for name, value in vars(template).items() if value is not None},
            )
    return GENERIC


def suggest_entries(text: str) -> tuple[Template, list[Suggestion]]:
    # Layout extraction pads columns with long runs of spaces; collapse them so
    # a label and its value in another column are still close together.
    text = re.sub(r"[^\S\n]+", " ", text)
    template = select_template(text)
    medium = template.medium or guess_medium(text)
    suggestions: list[Suggestion] = []

    period_start = period_end = None
    if match := template.period.search(text):
        try:
            period_start, period_end = parse_german_date(match[1]), parse_german_date(match[2])
        except ValueError:
            pass

    if period_end:
        for match in template.consumption.finditer(text):
            suggestions.append(
                Suggestion(
                    kind="verbrauch",
                    medium=medium,
                    period_start=period_start,
                    period_end=period_end,
                    value=parse_german_number(match[1]),
                    unit=normalize_unit(match[2]),
                    source=match[0].strip(),
                )
            )

        # Only one amount is pre-selected as the bill's cost: the gross total if
        # present, else the invoice amount (which may already net out payments).
        totals = list(template.total.finditer(text))
        preferred = next((m for m in totals if m[1].lower() == "gesamtbetrag"), totals[0] if totals else None)
        for match in totals:
            suggestions.append(
                Suggestion(
                    kind="betrag",
                    medium=medium,
                    period_start=period_start,
                    period_end=period_end,
                    value=parse_german_number(match[2]),
                    unit="EUR",
                    source=match[0].strip(),
                    checked=match is preferred,
                )
            )
        # Payments have no period of their own; dated at the period end they
        # count as "Abbuchungen im Zeitraum". Not pre-selected, because bank
        # exports usually provide the individual debits.
        for match in template.debit.finditer(text):
            suggestions.append(
                Suggestion(
                    kind="betrag",
                    medium=medium,
                    period_start=None,
                    period_end=period_end,
                    value=parse_german_number(match[2]),
                    unit="EUR",
                    source=match[0].strip(),
                    checked=False,
                )
            )

    for match in template.meter_reading.finditer(text):
        try:
            reading_date = parse_german_date(match[1])
        except ValueError:
            continue
        unit = normalize_unit(match[3]) if match[3] else ("m³" if medium in ("gas", "wasser") else "kWh")
        suggestions.append(
            Suggestion(
                kind="zaehlerstand",
                medium=medium,
                period_start=None,
                period_end=reading_date,
                value=parse_german_number(match[2]),
                unit=unit,
                source=match[0].strip(),
            )
        )

    return template, _dedupe(suggestions)


def _dedupe(suggestions: list[Suggestion]) -> list[Suggestion]:
    seen = set()
    unique = []
    for suggestion in suggestions:
        key = (suggestion.kind, suggestion.period_start, suggestion.period_end, suggestion.value, suggestion.unit)
        if key not in seen:
            seen.add(key)
            unique.append(suggestion)
    return unique
