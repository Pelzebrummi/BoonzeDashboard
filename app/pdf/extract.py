import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.pdf.worker import EXIT_ENCRYPTED

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ExtractionError(Exception):
    """The PDF could not be read; the message is shown to the user."""


@dataclass(frozen=True)
class ExtractedPdf:
    pages: list[str]
    page_count: int

    @property
    def text(self) -> str:
        return "\n\f\n".join(self.pages)


def extract_text(path: Path, *, timeout: float, memory_limit_mb: int) -> ExtractedPdf:
    """Extract the text layer of a PDF in a resource-limited subprocess.

    Blocking; call it from a worker thread inside async code.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.pdf.worker", str(path), str(memory_limit_mb)],
            capture_output=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError("Das Auslesen der PDF hat zu lange gedauert.") from exc

    if result.returncode == EXIT_ENCRYPTED:
        raise ExtractionError("Die PDF ist passwortgeschützt.")
    if result.returncode != 0:
        raise ExtractionError("Die PDF konnte nicht gelesen werden (beschädigt oder zu groß?).")

    data = json.loads(result.stdout.decode("utf-8"))
    return ExtractedPdf(pages=data["pages"], page_count=data["page_count"])
