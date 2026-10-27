from app.pdf.extract import extract_text
from app.pdf.worker import _page_text
from tests.helpers import SAMPLE_BILL, make_pdf


class FakePage:
    def __init__(self, layout: str | Exception, plain: str | Exception) -> None:
        self.results = {"layout": layout, "plain": plain}

    def extract_text(self, extraction_mode: str) -> str:
        result = self.results[extraction_mode]
        if isinstance(result, Exception):
            raise result
        return result


def test_falls_back_to_plain_mode_when_layout_fails():
    assert _page_text(FakePage(ValueError("layout"), "Zählerstand")) == "Zählerstand"


def test_unreadable_page_gives_empty_text():
    assert _page_text(FakePage(ValueError(), KeyError())) == ""


def test_lone_surrogates_are_replaced():
    assert _page_text(FakePage("Rechnung \ud800B", "")) == "Rechnung ?B"


def test_extracts_text_in_a_subprocess(tmp_path):
    path = tmp_path / "bill.pdf"
    path.write_bytes(make_pdf(SAMPLE_BILL))
    extracted = extract_text(path, timeout=60, memory_limit_mb=512)
    assert extracted.page_count == 1
    assert "Gesamtverbrauch" in extracted.text
