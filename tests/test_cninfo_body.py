"""cninfo PDF 正文提取（v2）：注入固定 PDF 字节，绝不真打网。"""
from __future__ import annotations

import io

from pypdf import PdfWriter

from fd_info.fetchers.cninfo import fetch_body


def _pdf_bytes(text: str) -> bytes:
    # pypdf 的 writer 只能写空白页；用最小合法 PDF 手写含文本对象
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offsets:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def test_extracts_text_from_pdf():
    body = fetch_body("https://example.test/a.PDF", download=lambda url: _pdf_bytes("Hello CNINFO body"))
    assert body is not None
    assert "Hello CNINFO body" in body


def test_download_failure_returns_none():
    def boom(url: str) -> bytes:
        raise RuntimeError("network down")

    assert fetch_body("https://example.test/a.PDF", download=boom) is None


def test_empty_pdf_returns_none():
    assert fetch_body("https://example.test/a.PDF", download=lambda url: _pdf_bytes("   ")) is None


def test_blank_url_returns_none():
    assert fetch_body("") is None
