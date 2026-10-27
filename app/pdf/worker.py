"""Extract text from one PDF in a separate process.

Run as ``python -m app.pdf.worker <path> <memory_limit_mb>``. Malformed PDFs can
make PDF parsers loop or allocate without bound, and HA apps have no memory
limit of their own, so the parsing happens here under an address-space limit
while the parent enforces a timeout.
"""

import json
import sys

from pypdf import PageObject, PdfReader

MAX_PAGES = 50

EXIT_ENCRYPTED = 3


def _limit_memory(limit_mb: int) -> None:
    try:
        import resource
    except ImportError:  # Windows development machines
        return
    limit = limit_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _page_text(page: PageObject) -> str:
    # Layout mode keeps table rows on one line, but fails on some pages where
    # plain mode still works.
    for mode in ("layout", "plain"):
        try:
            text = page.extract_text(extraction_mode=mode)
            break
        except Exception:
            continue
    else:
        return ""
    # pypdf can return lone surrogates from broken ToUnicode maps; they cannot
    # be stored as UTF-8.
    return text.encode("utf-8", "replace").decode("utf-8")


def main() -> int:
    path, limit_mb = sys.argv[1], int(sys.argv[2])
    _limit_memory(limit_mb)

    reader = PdfReader(path)
    # Many bills are "encrypted" only to restrict printing/copying and open
    # with an empty password.
    if reader.is_encrypted and not reader.decrypt(""):
        return EXIT_ENCRYPTED

    pages = [_page_text(page) for page in reader.pages[:MAX_PAGES]]
    sys.stdout.buffer.write(
        json.dumps({"pages": pages, "page_count": len(reader.pages)}).encode("utf-8")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
