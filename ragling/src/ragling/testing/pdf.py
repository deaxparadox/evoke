"""Writes small, text-only PDFs byte for byte, so tests and benchmarks need no PDF library or binary fixtures."""

PAGE_W, PAGE_H = 612, 792  # US Letter, points
MARGIN, FONT_SIZE, LEADING, PARA_GAP = 72, 12, 14, 14


def _esc(line: str) -> str:
    if not line.isascii():
        raise ValueError("make_text_pdf supports ASCII text only (Helvetica, no encoding table)")
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content(paragraphs: list[str]) -> bytes:
    """Lines inside a paragraph are LEADING apart; paragraphs get an extra PARA_GAP between them."""
    ops, y = [], PAGE_H - MARGIN
    for n, para in enumerate(paragraphs):
        if n:
            y -= PARA_GAP
        for line in para.split("\n"):
            ops.append(f"BT /F1 {FONT_SIZE} Tf {MARGIN} {y} Td ({_esc(line)}) Tj ET")
            y -= LEADING
    return "\n".join(ops).encode()


def make_text_pdf(pages: list[list[str]]) -> bytes:
    """`pages[i]` is a list of paragraphs (a paragraph may contain `\\n` line breaks). An empty list is a blank page."""
    n = len(pages)
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_obj = 3 + 2 * n
    for i, paras in enumerate(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {4 + 2 * i} 0 R >>".encode()
        )
        body = _content(paras)
        objs.append(b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)
