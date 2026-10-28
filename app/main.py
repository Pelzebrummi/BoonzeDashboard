"""Web app served through Home Assistant ingress.

Ingress serves the app under /api/hassio_ingress/<token>/ and passes that prefix
in the X-Ingress-Path header. Every link, form action and redirect is therefore
built from ``base`` (see ``render`` and ``redirect``), never from a bare "/".
"""

import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, time, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import db
from app.config import Settings, load_settings
from app.ha import HAError, HomeAssistant, PeriodQuery
from app.parsing import parse_german_number, suggest_entries
from app.pdf.extract import ExtractionError, extract_text

APP_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=APP_DIR / "templates")
INGRESS_PATH = re.compile(r"^/api/hassio_ingress/[A-Za-z0-9_-]+$")
SAFE_NEXT = re.compile(r"^/(documents/\d+)?$")
CHUNK_SIZE = 1024 * 1024
FORM_OVERHEAD = 1024 * 1024
EMPTY_ROWS = 2
# Appended to stylesheet URLs so a new release is not served from the browser cache.
ASSET_VERSION = os.environ.get("APP_VERSION", "dev")

# Deviation of the measured from the billed consumption, in percent (design system: StatusPill).
STATUS_OK_PERCENT = 5.0
STATUS_WARN_PERCENT = 15.0
STATUS_LABELS = {
    "ok": ("✓", "passt"),
    "warn": ("!", "prüfen"),
    "bad": ("×", "weicht ab"),
    "none": ("–", "unvollständig"),
}


def _de_date(value: str | date | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value.strftime("%d.%m.%Y")


def _german_separators(text: str) -> str:
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _de_number(value: float | None, digits: int = 3) -> str:
    """Display format with thousands separators: 2111 -> '2.111', 0.38484 -> '0,3848' (digits=4)."""
    if value is None:
        return ""
    # "z" turns a value that rounds to zero into 0 instead of -0.
    text = f"{value:z,.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return _german_separators(text).replace("-", "−")


def _de_input(value: float | None, unit: str | None = None) -> str:
    """Form field format without thousands separators, so '1.234' is never ambiguous: 2111.5 -> '2111,5'.

    Euro amounts keep two decimals: 812.4 -> '812,40'.
    """
    if value is None:
        return ""
    if unit == "EUR":
        return f"{value:z.2f}".replace(".", ",")
    return f"{value:z.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _de_money(value: float | None) -> str:
    return "" if value is None else _german_separators(f"{value:z,.2f}").replace("-", "−")


TEMPLATES.env.filters["de_date"] = _de_date
TEMPLATES.env.filters["de_number"] = _de_number
TEMPLATES.env.filters["de_input"] = _de_input
TEMPLATES.env.filters["de_money"] = _de_money


def classify_deviation(deviation: float) -> str:
    # Rounded like the percentage in verdict_text, so pill and sentence never disagree (5,0 % is "passt").
    size = round(abs(deviation), 1)
    if size <= STATUS_OK_PERCENT:
        return "ok"
    if size <= STATUS_WARN_PERCENT:
        return "warn"
    return "bad"


def verdict_text(deviation: float) -> str:
    size = abs(deviation)
    if round(size, 1) == 0:
        return "Messung und Rechnung stimmen überein."
    direction = "weniger" if deviation < 0 else "mehr"
    return f"Home Assistant hat {_de_number(size, 1)} % {direction} gemessen als abgerechnet."


class BodySizeLimit:
    """Abort request bodies above ``max_bytes`` while they stream in.

    With ``ingress_stream: true`` the Supervisor forwards uploads chunked and
    without Content-Length, so the size is only known by counting.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # HTTPException passes through FastAPI's body parsing unchanged.
                    raise HTTPException(413, "Die Datei ist zu groß.")
            return message

        await self.app(scope, limited_receive, send)


def ingress_base(request: Request) -> str:
    path = request.headers.get("X-Ingress-Path", "")
    return path if INGRESS_PATH.match(path) else ""


def current_user(request: Request) -> str | None:
    return request.headers.get("X-Remote-User-Name") or None


NAV_PAGES = frozenset({"/", "/upload", "/entries/new", "/settings"})


def nav_section(path: str) -> str:
    """Which navigation entry is current; documents belong to the overview."""
    for prefix, section in (("/upload", "upload"), ("/entries/new", "entry"), ("/settings", "settings")):
        if path.startswith(prefix):
            return section
    return "overview"


def nav_current(path: str) -> str:
    """aria-current value: "page" on the section's own page, "true" on pages inside it (a document)."""
    return "page" if path in NAV_PAGES else "true"


def render(
    request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200
) -> Response:
    return TEMPLATES.TemplateResponse(
        request,
        name,
        {
            "base": ingress_base(request),
            "nav": nav_section(request.url.path),
            "nav_current": nav_current(request.url.path),
            "asset_version": ASSET_VERSION,
            "status_labels": STATUS_LABELS,
            "user": request.headers.get("X-Remote-User-Display-Name") or current_user(request),
            "kinds": db.KINDS,
            "media": db.MEDIA_LABELS,
            "units": db.UNITS,
            **(context or {}),
        },
        status_code=status_code,
    )


def redirect(request: Request, path: str) -> RedirectResponse:
    return RedirectResponse(f"{ingress_base(request)}{path}", status_code=303)


def parse_entry(
    *,
    kind: str,
    medium: str,
    period_start: str,
    period_end: str,
    value: str,
    unit: str,
    note: str = "",
) -> dict[str, Any]:
    """Validate one entry from a form; raises HTTPException(400) with a readable message."""
    if kind not in db.KINDS:
        raise HTTPException(400, "Unbekannte Art des Eintrags.")
    if medium not in db.MEDIA_LABELS:
        raise HTTPException(400, "Unbekanntes Medium.")
    if unit not in db.UNITS:
        raise HTTPException(400, "Unbekannte Einheit.")
    try:
        end = date.fromisoformat(period_end)
        start = date.fromisoformat(period_start) if period_start else None
    except ValueError as exc:
        raise HTTPException(400, "Ungültiges Datum.") from exc
    if kind == "verbrauch" and start is None:
        raise HTTPException(400, "Ein Verbrauch braucht einen Zeitraum (von und bis).")
    if kind == "zaehlerstand" and start is not None:
        raise HTTPException(400, "Ein Zählerstand hat nur ein Datum (bis).")
    if start and start > end:
        raise HTTPException(400, "Der Zeitraum beginnt nach seinem Ende.")
    # Otherwise a Betrag in kWh would be summed as euros into the card's costs.
    if (kind == "betrag") != (unit == "EUR"):
        raise HTTPException(400, "Ein Betrag steht in EUR, ein Verbrauch oder Zählerstand in kWh oder m³.")
    try:
        number = parse_german_number(value)
    except ValueError as exc:
        raise HTTPException(400, f"Ungültige Zahl: {value}") from exc
    return {
        "kind": kind,
        "medium": medium,
        "period_start": start.isoformat() if start else None,
        "period_end": end.isoformat(),
        "value": number,
        "unit": unit,
        "note": note.strip() or None,
    }


def parse_entry_rows(form: FormData) -> list[dict[str, Any]]:
    """Read the review form: rows-<n>-<field>, a row is saved only if rows-<n>-use is ticked."""
    indexes = sorted(
        {int(match[1]) for key in form.keys() if (match := re.fullmatch(r"rows-(\d+)-use", key))}
    )
    return [
        parse_entry(
            **{
                field: str(form.get(f"rows-{index}-{field}", ""))
                for field in ("kind", "medium", "period_start", "period_end", "value", "unit", "note")
            }
        )
        for index in indexes
    ]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    ha = HomeAssistant(settings.ha_ws_url, settings.ha_token)
    tz = ZoneInfo(settings.timezone)

    def connection() -> closing[sqlite3.Connection]:
        return closing(db.connect(settings.db_path))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.migrate(settings.db_path)
        settings.documents_dir.mkdir(parents=True, exist_ok=True)
        for leftover in settings.documents_dir.glob("*.part"):
            leftover.unlink()
        yield

    app = FastAPI(title="Energie Dashboard", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_upload_bytes + FORM_OVERHEAD)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @app.middleware("http")
    async def only_through_ingress(request: Request, call_next):
        if settings.allowed_clients and (
            request.client is None or request.client.host not in settings.allowed_clients
        ):
            return PlainTextResponse("Nur über Home Assistant erreichbar.", status_code=403)
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def show_error(request: Request, exc: StarletteHTTPException) -> Response:
        message = exc.detail
        # Starlette's own errors carry the English status phrase ("Not Found").
        if message == HTTPStatus(exc.status_code).phrase:
            message = {404: "Diese Seite gibt es nicht.", 405: "Diese Aktion ist hier nicht möglich."}.get(
                exc.status_code, "Die Anfrage konnte nicht bearbeitet werden."
            )
        return render(request, "error.html", {"message": message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def show_validation_error(request: Request, exc: RequestValidationError) -> Response:
        return render(
            request,
            "error.html",
            {"message": "Ungültige Anfrage. Füll das Formular noch einmal aus und sende es ab."},
            status_code=422,
        )

    @app.get("/healthz")
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/")
    async def index(request: Request) -> Response:
        with connection() as conn:
            entries = db.list_entries(conn)
            documents = db.list_documents(conn)
        comparisons, ha_error = await compare_with_ha(entries)
        return render(
            request,
            "index.html",
            {
                "entries": entries,
                "documents": documents,
                "comparisons": comparisons,
                "ha_error": ha_error,
                "statistic_ids": settings.statistic_ids,
            },
        )

    async def compare_with_ha(entries: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], str | None]:
        consumption = [e for e in entries if e["kind"] == "verbrauch"]
        amounts = [e for e in entries if e["kind"] == "betrag"]

        queries: list[tuple[int, PeriodQuery]] = []
        for entry in consumption:
            statistic_id = settings.statistic_ids.get(entry["medium"])
            if statistic_id:
                # The bill's end date is inclusive, so the period ends at the next local midnight.
                start = datetime.combine(date.fromisoformat(entry["period_start"]), time.min, tz)
                end = datetime.combine(date.fromisoformat(entry["period_end"]) + timedelta(days=1), time.min, tz)
                queries.append((entry["id"], PeriodQuery(statistic_id, start, end)))

        changes: dict[int, Any] = {}
        ha_error = None
        if queries:
            try:
                results = await ha.period_changes([query for _, query in queries])
                changes = {entry_id: result for (entry_id, _), result in zip(queries, results)}
            except HAError as exc:
                ha_error = str(exc)

        rows = []
        for entry in consumption:
            change = changes.get(entry["id"])
            deviation = None
            if (
                change
                and change.covered
                and change.change is not None
                and change.unit == entry["unit"]
                and entry["value"] > 0
            ):
                deviation = (change.change - entry["value"]) / entry["value"] * 100
            same_medium = [a for a in amounts if a["medium"] == entry["medium"]]
            # A bill amount covers exactly the same period; a debit is a single date inside it.
            # ISO dates compare correctly as strings.
            billed = [
                a["value"]
                for a in same_medium
                if a["period_start"] == entry["period_start"] and a["period_end"] == entry["period_end"]
            ]
            debited = sum(
                a["value"]
                for a in same_medium
                if a["period_start"] is None and entry["period_start"] <= a["period_end"] <= entry["period_end"]
            )
            status, status_label, verdict = None, None, None
            if deviation is not None:
                status = classify_deviation(deviation)
                verdict = verdict_text(deviation)
            elif settings.statistic_ids.get(entry["medium"]) and not ha_error:
                status = "none"
                if change is None or change.change is None:
                    verdict = "Home Assistant hat für diesen Zeitraum keine Werte."
                elif not change.covered:
                    verdict = "Home Assistant hat diesen Zeitraum nicht vollständig gemessen."
                elif change.unit != entry["unit"]:
                    status_label = "nicht vergleichbar"
                    measured = f"misst in {change.unit}" if change.unit else "nennt keine Einheit"
                    verdict = f"Home Assistant {measured}, die Rechnung nennt {entry['unit']}."
                else:
                    status_label = "nicht vergleichbar"
                    verdict = (
                        f"Die Rechnung nennt {_de_number(entry['value'])} {entry['unit']}, "
                        "daraus lässt sich keine Abweichung berechnen."
                    )
            rows.append(
                {
                    "entry": entry,
                    "ha": change,
                    "deviation": deviation,
                    "status": status,
                    "status_label": status_label,
                    "verdict": verdict,
                    "billed": sum(billed) if billed else None,
                    "billed_count": len(billed),
                    "debited": debited or None,
                    "price": sum(billed) / entry["value"] if billed and entry["value"] > 0 else None,
                }
            )
        return rows, ha_error

    @app.get("/upload")
    def upload_form(request: Request) -> Response:
        return render(request, "upload.html", {"max_upload_mb": settings.max_upload_bytes // (1024 * 1024)})

    @app.post("/upload")
    async def upload(request: Request, file: UploadFile) -> Response:
        part = settings.documents_dir / f"{secrets.token_hex(8)}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with part.open("wb") as out:
                while chunk := await file.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "Die Datei ist zu groß.")
                    digest.update(chunk)
                    out.write(chunk)
            with part.open("rb") as stored:
                if not stored.read(1024).lstrip().startswith(b"%PDF-"):
                    raise HTTPException(400, "Das ist keine PDF-Datei.")

            sha256 = digest.hexdigest()
            with connection() as conn:
                existing = db.find_document_by_sha(conn, sha256)
            if existing:
                return redirect(request, f"/documents/{existing['id']}?duplicate=1")

            try:
                extracted = await run_in_threadpool(
                    extract_text,
                    part,
                    timeout=settings.pdf_timeout_seconds,
                    memory_limit_mb=settings.pdf_memory_limit_mb,
                )
            except ExtractionError as exc:
                raise HTTPException(422, str(exc)) from exc

            target = settings.documents_dir / f"{sha256}.pdf"
            part.replace(target)
            try:
                with connection() as conn:
                    document_id = db.insert_document(
                        conn,
                        filename=Path(file.filename or "dokument.pdf").name[:200],
                        sha256=sha256,
                        size_bytes=size,
                        page_count=extracted.page_count,
                        text=extracted.text,
                        uploaded_by=current_user(request),
                    )
            except sqlite3.IntegrityError:
                # The same file was uploaded concurrently; both requests end on the same document.
                with connection() as conn:
                    existing = db.find_document_by_sha(conn, sha256)
                return redirect(request, f"/documents/{existing['id']}?duplicate=1")
            except BaseException:
                # No row points to the file, so it would be orphaned.
                with connection() as conn:
                    if db.find_document_by_sha(conn, sha256) is None:
                        target.unlink(missing_ok=True)
                raise
        finally:
            part.unlink(missing_ok=True)
        return redirect(request, f"/documents/{document_id}")

    @app.get("/documents/{document_id}")
    def document(request: Request, document_id: int, duplicate: int = 0) -> Response:
        with connection() as conn:
            doc = db.get_document(conn, document_id)
            if doc is None:
                raise HTTPException(404, "Dokument nicht gefunden.")
            entries = db.list_entries(conn, document_id)
        template, suggestions = suggest_entries(doc["text"])
        return render(
            request,
            "document.html",
            {
                "doc": doc,
                "entries": entries,
                "template": template,
                "suggestions": suggestions,
                "empty_rows": EMPTY_ROWS,
                "duplicate": duplicate,
            },
        )

    @app.get("/documents/{document_id}/pdf")
    def document_pdf(document_id: int) -> FileResponse:
        with connection() as conn:
            doc = db.get_document(conn, document_id)
        if doc is None:
            raise HTTPException(404, "Dokument nicht gefunden.")
        return FileResponse(
            settings.documents_dir / f"{doc['sha256']}.pdf",
            media_type="application/pdf",
            filename=doc["filename"],
            content_disposition_type="inline",
        )

    @app.post("/documents/{document_id}/entries")
    async def save_document_entries(request: Request, document_id: int) -> Response:
        rows = parse_entry_rows(await request.form())
        with connection() as conn:
            if db.get_document(conn, document_id) is None:
                raise HTTPException(404, "Dokument nicht gefunden.")
            db.insert_entries(conn, rows, document_id=document_id, created_by=current_user(request))
        return redirect(request, f"/documents/{document_id}")

    @app.post("/documents/{document_id}/delete")
    def delete_document(request: Request, document_id: int) -> Response:
        with connection() as conn:
            doc = db.get_document(conn, document_id)
            if doc is None:
                raise HTTPException(404, "Dokument nicht gefunden.")
            db.delete_document(conn, document_id)
        (settings.documents_dir / f"{doc['sha256']}.pdf").unlink(missing_ok=True)
        return redirect(request, "/")

    @app.get("/entries/new")
    def new_entry(request: Request) -> Response:
        return render(request, "entry_new.html")

    @app.post("/entries")
    async def create_entry(request: Request) -> Response:
        form = await request.form()
        row = parse_entry(
            **{
                field: str(form.get(field, ""))
                for field in ("kind", "medium", "period_start", "period_end", "value", "unit", "note")
            }
        )
        with connection() as conn:
            db.insert_entry(conn, created_by=current_user(request), **row)
        return redirect(request, "/")

    @app.post("/entries/{entry_id}/delete")
    async def delete_entry(request: Request, entry_id: int) -> Response:
        form = await request.form()
        next_path = str(form.get("next", "/"))
        with connection() as conn:
            db.delete_entry(conn, entry_id)
        return redirect(request, next_path if SAFE_NEXT.match(next_path) else "/")

    @app.get("/settings")
    async def statistics(request: Request) -> Response:
        available: list[dict[str, Any]] = []
        ha_error = None
        try:
            available = await ha.list_sum_statistics()
        except HAError as exc:
            ha_error = str(exc)
        return render(
            request,
            "settings.html",
            {"available": available, "ha_error": ha_error, "statistic_ids": settings.statistic_ids},
        )

    return app
