import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from websockets.sync.server import ServerConnection, serve

SAMPLE_BILL = [
    "Stadtwerke Musterstadt GmbH",
    "Jahresabrechnung Strom",
    "Abrechnungszeitraum: 01.01.2025 - 31.12.2025",
    "Zählerstand am 31.12.2025: 12.345 kWh",
    "Gesamtverbrauch 2.111 kWh",
    "Rechnungsbetrag 812,40 €",
]


def make_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF with a real text layer."""
    content = ["BT", "/F1 11 Tf", "14 TL", "50 800 Td"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.append(f"({escaped}) Tj T*")
    content.append("ET")
    stream = "\n".join(content).encode("cp1252")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]"
        b" /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


NO_REPLY = object()


@contextmanager
def fake_home_assistant(
    respond: Callable[[dict[str, Any]], Any], token: str = "secret"
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Run a tiny HA WebSocket API; yields (url, received commands).

    ``respond`` returns the result for a command, or NO_REPLY to never answer.
    """
    received: list[dict[str, Any]] = []

    def handler(ws: ServerConnection) -> None:
        ws.send(json.dumps({"type": "auth_required", "ha_version": "2026.10.4"}))
        auth = json.loads(ws.recv())
        if auth.get("access_token") != token:
            ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
            return
        ws.send(json.dumps({"type": "auth_ok", "ha_version": "2026.10.4"}))
        for raw in ws:
            command = json.loads(raw)
            received.append(command)
            result = respond(command)
            if result is not NO_REPLY:
                ws.send(json.dumps({"id": command["id"], "type": "result", "success": True, "result": result}))

    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.socket.getsockname()[1]
        try:
            yield f"ws://127.0.0.1:{port}", received
        finally:
            server.shutdown()
            thread.join(timeout=5)
