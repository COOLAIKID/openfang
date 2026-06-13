from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path("/home/user/openfang/autoearn/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Optional library detection
# ---------------------------------------------------------------------------

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, cm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
        HRFlowable,
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    _REPORTLAB = True
except ImportError:
    _REPORTLAB = False

try:
    import pypdf
    _PYPDF = True
except ImportError:
    try:
        import PyPDF2 as pypdf  # type: ignore
        _PYPDF = True
    except ImportError:
        _PYPDF = False

try:
    import qrcode  # type: ignore
    _QRCODE = True
except ImportError:
    _QRCODE = False

try:
    import weasyprint  # type: ignore
    _WEASYPRINT = True
except ImportError:
    _WEASYPRINT = False

try:
    from pdf2image import convert_from_path  # type: ignore
    _PDF2IMAGE = True
except ImportError:
    _PDF2IMAGE = False


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _out_path(suggested: str, default_name: str) -> Path:
    """Resolve output path: use suggested if given, else generate in OUTPUT_DIR."""
    if suggested:
        p = Path(suggested)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return OUTPUT_DIR / default_name


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# 1. create_pdf_report
# ---------------------------------------------------------------------------

def create_pdf_report(
    title: str,
    sections: list[dict],
    output_path: str = "",
) -> str:
    """Generate a multi-section PDF report.

    sections: list of dicts with keys 'heading', 'content', 'type'
              type can be 'text', 'table', or 'list'.
    Returns absolute path to the created file.
    """
    out = _out_path(output_path, f"report_{_ts()}.pdf")

    if _REPORTLAB:
        doc = SimpleDocTemplate(str(out), pagesize=A4)
        styles = getSampleStyleSheet()
        story: list[Any] = []

        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontSize=22,
            spaceAfter=24,
            textColor=colors.HexColor("#1a1a2e"),
        )
        heading_style = ParagraphStyle(
            "SectionHeading",
            parent=styles["Heading2"],
            fontSize=14,
            spaceBefore=16,
            spaceAfter=8,
            textColor=colors.HexColor("#16213e"),
        )
        body_style = ParagraphStyle(
            "BodyText",
            parent=styles["Normal"],
            fontSize=11,
            leading=16,
            spaceAfter=8,
        )

        story.append(Paragraph(title, title_style))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
        story.append(Spacer(1, 0.2 * inch))

        for sec in sections:
            heading = sec.get("heading", "")
            content = sec.get("content", "")
            sec_type = sec.get("type", "text")

            if heading:
                story.append(Paragraph(heading, heading_style))

            if sec_type == "text":
                for para in str(content).split("\n\n"):
                    para = para.strip()
                    if para:
                        safe = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        story.append(Paragraph(safe, body_style))

            elif sec_type == "list":
                items = content if isinstance(content, list) else str(content).split("\n")
                for item in items:
                    item = str(item).strip()
                    if item:
                        safe = item.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        story.append(Paragraph(f"• {safe}", body_style))

            elif sec_type == "table":
                if isinstance(content, list) and content:
                    table_data = [[str(cell) for cell in row] for row in content]
                    t = Table(table_data, repeatRows=1)
                    t.setStyle(
                        TableStyle(
                            [
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213e")),
                                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                ("FONTSIZE", (0, 0), (-1, -1), 9),
                                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
                                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            ]
                        )
                    )
                    story.append(t)

            story.append(Spacer(1, 0.1 * inch))

        doc.build(story)

    else:
        # HTML fallback
        out = _out_path(output_path.replace(".pdf", ".html") if output_path else "", f"report_{_ts()}.html")
        lines = [
            "<!DOCTYPE html><html><head>",
            f"<title>{title}</title>",
            "<style>body{{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:20px;}}",
            "h1{{color:#1a1a2e;border-bottom:2px solid #ccc;padding-bottom:10px;}}",
            "h2{{color:#16213e;margin-top:24px;}}",
            "table{{border-collapse:collapse;width:100%;}}",
            "th{{background:#16213e;color:white;padding:8px;text-align:left;}}",
            "td{{padding:6px 8px;border:1px solid #ddd;}}",
            "tr:nth-child(even){{background:#f0f4f8;}}</style></head><body>",
            f"<h1>{title}</h1>",
        ]
        for sec in sections:
            heading = sec.get("heading", "")
            content = sec.get("content", "")
            sec_type = sec.get("type", "text")
            if heading:
                lines.append(f"<h2>{heading}</h2>")
            if sec_type == "text":
                for para in str(content).split("\n\n"):
                    para = para.strip()
                    if para:
                        lines.append(f"<p>{para}</p>")
            elif sec_type == "list":
                items = content if isinstance(content, list) else str(content).split("\n")
                lines.append("<ul>")
                for item in items:
                    if str(item).strip():
                        lines.append(f"<li>{item}</li>")
                lines.append("</ul>")
            elif sec_type == "table":
                if isinstance(content, list) and content:
                    lines.append("<table><thead><tr>")
                    for cell in content[0]:
                        lines.append(f"<th>{cell}</th>")
                    lines.append("</tr></thead><tbody>")
                    for row in content[1:]:
                        lines.append("<tr>")
                        for cell in row:
                            lines.append(f"<td>{cell}</td>")
                        lines.append("</tr>")
                    lines.append("</tbody></table>")
        lines.append("</body></html>")
        out.write_text("\n".join(lines), encoding="utf-8")

    return str(out)


# ---------------------------------------------------------------------------
# 2. create_pdf_invoice
# ---------------------------------------------------------------------------

def create_pdf_invoice(invoice_data: dict, output_path: str = "") -> str:
    """Generate a professional invoice PDF.

    invoice_data keys: number, date, from (dict with name/address/email),
    to (dict with name/address/email), items (list of {description, qty, unit_price}),
    total (float), notes (str).
    Returns path to created file.
    """
    out = _out_path(output_path, f"invoice_{invoice_data.get('number', _ts())}.pdf")

    inv_num = invoice_data.get("number", "INV-001")
    inv_date = invoice_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    from_info = invoice_data.get("from", {})
    to_info = invoice_data.get("to", {})
    items = invoice_data.get("items", [])
    total = invoice_data.get("total", 0.0)
    notes = invoice_data.get("notes", "")

    if _REPORTLAB:
        doc = SimpleDocTemplate(str(out), pagesize=letter, topMargin=0.75 * inch)
        styles = getSampleStyleSheet()
        story: list[Any] = []

        header_style = ParagraphStyle("InvHeader", parent=styles["Title"], fontSize=26,
                                       textColor=colors.HexColor("#1a1a2e"))
        normal = styles["Normal"]
        bold_style = ParagraphStyle("Bold", parent=normal, fontName="Helvetica-Bold")
        small = ParagraphStyle("Small", parent=normal, fontSize=9, leading=13)

        story.append(Paragraph("INVOICE", header_style))
        story.append(Spacer(1, 0.1 * inch))

        meta_data = [
            ["Invoice Number:", inv_num, "Date:", inv_date],
        ]
        meta_table = Table(meta_data, colWidths=[1.5 * inch, 2 * inch, 1 * inch, 2 * inch])
        meta_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 0.25 * inch))

        def _party_block(label: str, info: dict) -> Table:
            rows = [[label]]
            rows.append([info.get("name", "")])
            addr = info.get("address", "")
            if addr:
                rows.append([addr])
            email = info.get("email", "")
            if email:
                rows.append([email])
            t = Table(rows, colWidths=[3 * inch])
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            return t

        party_table = Table(
            [[_party_block("FROM", from_info), _party_block("BILL TO", to_info)]],
            colWidths=[3.5 * inch, 3.5 * inch],
        )
        story.append(party_table)
        story.append(Spacer(1, 0.3 * inch))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
        story.append(Spacer(1, 0.15 * inch))

        table_data = [["Description", "Qty", "Unit Price", "Amount"]]
        subtotal = 0.0
        for item in items:
            qty = float(item.get("qty", 1))
            unit = float(item.get("unit_price", 0))
            amount = qty * unit
            subtotal += amount
            table_data.append([
                item.get("description", ""),
                str(qty),
                f"${unit:,.2f}",
                f"${amount:,.2f}",
            ])

        table_data.append(["", "", "Subtotal:", f"${subtotal:,.2f}"])
        table_data.append(["", "", "TOTAL:", f"${float(total):,.2f}"])

        items_table = Table(table_data, colWidths=[4 * inch, 0.75 * inch, 1.25 * inch, 1.25 * inch])
        items_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("GRID", (0, 0), (-1, -2), 0.5, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -3), [colors.white, colors.HexColor("#f5f5f5")]),
            ("FONTNAME", (2, -2), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (2, -1), (-1, -1), 12),
            ("LINEABOVE", (2, -2), (-1, -2), 1, colors.grey),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(items_table)

        if notes:
            story.append(Spacer(1, 0.3 * inch))
            story.append(Paragraph("<b>Notes:</b>", bold_style))
            story.append(Paragraph(notes, small))

        doc.build(story)

    else:
        out = _out_path(
            output_path.replace(".pdf", ".html") if output_path else "",
            f"invoice_{inv_num}.html",
        )
        rows_html = ""
        subtotal = 0.0
        for item in items:
            qty = float(item.get("qty", 1))
            unit = float(item.get("unit_price", 0))
            amount = qty * unit
            subtotal += amount
            rows_html += f"<tr><td>{item.get('description','')}</td><td>{qty}</td><td>${unit:,.2f}</td><td>${amount:,.2f}</td></tr>"

        html = f"""<!DOCTYPE html><html><head><title>Invoice {inv_num}</title>
<style>body{{font-family:Arial,sans-serif;max-width:800px;margin:40px auto;padding:20px;}}
h1{{color:#1a1a2e;}}table{{border-collapse:collapse;width:100%;margin-top:20px;}}
th{{background:#1a1a2e;color:white;padding:10px;text-align:left;}}
td{{padding:8px 10px;border:1px solid #ddd;}}tr:nth-child(even){{background:#f5f5f5;}}
.total{{font-weight:bold;font-size:1.1em;}}.parties{{display:flex;gap:60px;margin:20px 0;}}
</style></head><body>
<h1>INVOICE</h1>
<p><b>Invoice #:</b> {inv_num} &nbsp;&nbsp; <b>Date:</b> {inv_date}</p>
<div class="parties">
  <div><b>FROM</b><br>{from_info.get('name','')}<br>{from_info.get('address','')}<br>{from_info.get('email','')}</div>
  <div><b>BILL TO</b><br>{to_info.get('name','')}<br>{to_info.get('address','')}<br>{to_info.get('email','')}</div>
</div>
<table><thead><tr><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr></thead>
<tbody>{rows_html}</tbody>
<tfoot><tr><td colspan="3" style="text-align:right;"><b>Subtotal</b></td><td>${subtotal:,.2f}</td></tr>
<tr class="total"><td colspan="3" style="text-align:right;">TOTAL</td><td>${float(total):,.2f}</td></tr>
</tfoot></table>
{'<p><b>Notes:</b> '+notes+'</p>' if notes else ''}
</body></html>"""
        out.write_text(html, encoding="utf-8")

    return str(out)


# ---------------------------------------------------------------------------
# 3. create_pdf_ebook
# ---------------------------------------------------------------------------

def create_pdf_ebook(
    title: str,
    chapters: list[dict],
    author: str,
    output_path: str = "",
) -> str:
    """Generate an ebook-style PDF with cover page, TOC, and chapters.

    chapters: list of dicts with 'title' and 'content' keys.
    Returns path to created file.
    """
    out = _out_path(output_path, f"ebook_{_ts()}.pdf")

    if _REPORTLAB:
        doc = SimpleDocTemplate(str(out), pagesize=A4,
                                leftMargin=1.25 * inch, rightMargin=1.25 * inch,
                                topMargin=1 * inch, bottomMargin=1 * inch)
        styles = getSampleStyleSheet()
        story: list[Any] = []

        cover_title = ParagraphStyle("CoverTitle", parent=styles["Title"], fontSize=32,
                                      leading=40, spaceAfter=20, textColor=colors.HexColor("#1a1a2e"),
                                      alignment=TA_CENTER)
        cover_author = ParagraphStyle("CoverAuthor", parent=styles["Normal"], fontSize=16,
                                       alignment=TA_CENTER, textColor=colors.HexColor("#555555"))
        ch_heading = ParagraphStyle("ChapterHeading", parent=styles["Heading1"], fontSize=18,
                                     spaceBefore=0, spaceAfter=14, textColor=colors.HexColor("#1a1a2e"),
                                     pageBreakBefore=True)
        body = ParagraphStyle("EbookBody", parent=styles["Normal"], fontSize=11, leading=18,
                               spaceAfter=10, alignment=4)  # 4 = justify

        # Cover
        story.append(Spacer(1, 2 * inch))
        story.append(Paragraph(title, cover_title))
        story.append(Spacer(1, 0.5 * inch))
        story.append(Paragraph(f"by {author}", cover_author))
        story.append(Spacer(1, 0.25 * inch))
        story.append(Paragraph(datetime.now().strftime("%Y"), cover_author))
        story.append(PageBreak())

        # Table of Contents
        toc_heading = ParagraphStyle("TocHeading", parent=styles["Heading1"], fontSize=16,
                                      spaceAfter=16, textColor=colors.HexColor("#1a1a2e"))
        toc_item = ParagraphStyle("TocItem", parent=styles["Normal"], fontSize=11, leading=20)
        story.append(Paragraph("Table of Contents", toc_heading))
        for i, ch in enumerate(chapters, 1):
            story.append(Paragraph(f"{i}. {ch.get('title','')}", toc_item))
        story.append(PageBreak())

        # Chapters
        for i, ch in enumerate(chapters, 1):
            story.append(Paragraph(f"Chapter {i}: {ch.get('title', '')}", ch_heading))
            content = ch.get("content", "")
            for para_text in content.split("\n\n"):
                para_text = para_text.strip()
                if para_text:
                    safe = para_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    story.append(Paragraph(safe, body))

        doc.build(story)

    else:
        out = _out_path(
            output_path.replace(".pdf", ".html") if output_path else "",
            f"ebook_{_ts()}.html",
        )
        ch_html = ""
        for i, ch in enumerate(chapters, 1):
            ch_html += f"<h2>Chapter {i}: {ch.get('title','')}</h2>"
            for para in ch.get("content", "").split("\n\n"):
                para = para.strip()
                if para:
                    ch_html += f"<p>{para}</p>"

        toc_html = "".join(
            f"<li>{i}. {ch.get('title','')}</li>" for i, ch in enumerate(chapters, 1)
        )

        html = f"""<!DOCTYPE html><html><head><title>{title}</title>
<style>body{{font-family:Georgia,serif;max-width:700px;margin:60px auto;padding:20px;line-height:1.8;}}
h1{{text-align:center;font-size:2.5em;color:#1a1a2e;margin-bottom:0;}}
.author{{text-align:center;color:#555;font-size:1.2em;margin-top:10px;}}
h2{{color:#1a1a2e;margin-top:50px;border-bottom:1px solid #ccc;padding-bottom:6px;}}
p{{text-align:justify;}}ol{{line-height:2;}}
</style></head><body>
<h1>{title}</h1><p class="author">by {author}</p>
<hr style="margin:40px 0;"/>
<h2>Table of Contents</h2><ol>{toc_html}</ol>
{ch_html}
</body></html>"""
        out.write_text(html, encoding="utf-8")

    return str(out)


# ---------------------------------------------------------------------------
# 4. parse_pdf_text
# ---------------------------------------------------------------------------

def parse_pdf_text(path: str) -> str:
    """Extract and return all text from a PDF file using pypdf."""
    pdf_path = Path(path)
    if not pdf_path.exists():
        return f"Error: File not found: {path}"

    if _PYPDF:
        try:
            reader = pypdf.PdfReader(str(pdf_path))
            parts: list[str] = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
            return "\n\n".join(parts)
        except Exception as exc:
            return f"Error parsing PDF: {exc}"

    # Fallback: try pdftotext CLI
    try:
        result = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return result.stdout
        return f"Error: pdftotext failed: {result.stderr}"
    except FileNotFoundError:
        return "Error: pypdf not installed and pdftotext CLI not found."


# ---------------------------------------------------------------------------
# 5. pdf_page_count
# ---------------------------------------------------------------------------

def pdf_page_count(path: str) -> int:
    """Return the number of pages in a PDF file."""
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    if _PYPDF:
        reader = pypdf.PdfReader(str(pdf_path))
        return len(reader.pages)

    # Fallback: parse raw PDF bytes for /Type /Page entries
    data = pdf_path.read_bytes()
    # Count occurrences of '/Type /Page' which appears for each page
    count = len(re.findall(rb"/Type\s*/Page[^s]", data))
    if count:
        return count

    # Try pdfinfo CLI
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True, text=True, timeout=15
        )
        match = re.search(r"Pages:\s*(\d+)", result.stdout)
        if match:
            return int(match.group(1))
    except FileNotFoundError:
        pass

    return -1


# ---------------------------------------------------------------------------
# 6. merge_pdfs
# ---------------------------------------------------------------------------

def merge_pdfs(input_paths: list[str], output_path: str) -> str:
    """Merge multiple PDF files into one. Returns path to merged file."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if _PYPDF:
        writer = pypdf.PdfWriter()
        for p in input_paths:
            reader = pypdf.PdfReader(p)
            for page in reader.pages:
                writer.add_page(page)
        with open(str(out), "wb") as f:
            writer.write(f)
        return str(out)

    # Fallback: try pdfunite CLI (poppler)
    try:
        subprocess.run(
            ["pdfunite"] + input_paths + [str(out)],
            check=True, timeout=60
        )
        return str(out)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"PDF merge failed: pypdf not installed and pdfunite unavailable. {exc}")


# ---------------------------------------------------------------------------
# 7. split_pdf
# ---------------------------------------------------------------------------

def split_pdf(path: str, pages: list[int], output_path: str = "") -> str:
    """Extract specific pages (1-indexed) from a PDF. Returns path to new file."""
    out = _out_path(output_path, f"split_{Path(path).stem}_{_ts()}.pdf")

    if _PYPDF:
        reader = pypdf.PdfReader(path)
        writer = pypdf.PdfWriter()
        total = len(reader.pages)
        for page_num in pages:
            idx = page_num - 1
            if 0 <= idx < total:
                writer.add_page(reader.pages[idx])
        with open(str(out), "wb") as f:
            writer.write(f)
        return str(out)

    # Fallback: pdftk CLI
    pages_str = " ".join(str(p) for p in pages)
    try:
        subprocess.run(
            ["pdftk", path, "cat", pages_str, "output", str(out)],
            check=True, timeout=60
        )
        return str(out)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"PDF split failed: pypdf not installed and pdftk unavailable. {exc}")


# ---------------------------------------------------------------------------
# 8. html_to_pdf
# ---------------------------------------------------------------------------

def html_to_pdf(html_content: str, output_path: str = "") -> str:
    """Convert HTML string to PDF. Uses WeasyPrint if available, else wkhtmltopdf CLI."""
    out = _out_path(output_path, f"html2pdf_{_ts()}.pdf")

    if _WEASYPRINT:
        weasyprint.HTML(string=html_content).write_pdf(str(out))
        return str(out)

    # wkhtmltopdf via temp file
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False, encoding="utf-8") as tmp:
        tmp.write(html_content)
        tmp_path = tmp.name

    try:
        subprocess.run(
            ["wkhtmltopdf", tmp_path, str(out)],
            check=True, timeout=60, capture_output=True
        )
        return str(out)
    except FileNotFoundError:
        # Last resort: save as HTML
        html_out = out.with_suffix(".html")
        html_out.write_text(html_content, encoding="utf-8")
        return str(html_out)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 9. generate_qr_code
# ---------------------------------------------------------------------------

def generate_qr_code(data: str, output_path: str = "") -> str:
    """Generate a QR code for the given data string.

    Uses the qrcode library if available, otherwise generates a minimal SVG QR code.
    Returns path to the PNG (or SVG fallback) file.
    """
    if _QRCODE:
        out = _out_path(output_path, f"qr_{_ts()}.png")
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                            box_size=10, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(str(out))
        return str(out)

    # SVG fallback using a simple QR-like visual (actual QR encoding is complex;
    # this produces a data-URI SVG placeholder pointing to a QR API)
    out = _out_path(output_path.replace(".png", ".svg") if output_path else "", f"qr_{_ts()}.svg")
    import urllib.parse
    encoded = urllib.parse.quote(data)
    # Use Google Charts QR API as embedded image in SVG
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="200" height="220" viewBox="0 0 200 220">
  <rect width="200" height="220" fill="white"/>
  <image href="https://chart.googleapis.com/chart?cht=qr&amp;chs=200x200&amp;chl={encoded}"
         x="0" y="0" width="200" height="200"/>
  <text x="100" y="215" text-anchor="middle" font-size="9" fill="#555">
    {data[:40]}{'...' if len(data) > 40 else ''}
  </text>
</svg>"""
    out.write_text(svg, encoding="utf-8")
    return str(out)


# ---------------------------------------------------------------------------
# 10. create_simple_pdf
# ---------------------------------------------------------------------------

def create_simple_pdf(
    content_lines: list[str],
    output_path: str = "",
    title: str = "",
) -> str:
    """Create a minimal PDF using raw PDF structure — no external libraries required.

    Writes PDF 1.4 compatible bytes manually using deflate-compressed streams.
    Returns path to created file.
    """
    out = _out_path(output_path, f"simple_{_ts()}.pdf")

    def _encode_text(text: str) -> bytes:
        """Encode text for PDF string (escape parens and backslash)."""
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", errors="replace")

    # Build page content stream
    lines_to_render: list[str] = []
    if title:
        lines_to_render.append(f"TITLE: {title}")
        lines_to_render.append("")
    lines_to_render.extend(content_lines)

    # PDF uses points; A4 = 595 x 842 pts, margins ~50pt, line height ~14pt
    page_width, page_height = 595, 842
    margin_x, margin_top = 50, 800
    line_height = 14
    font_size_title = 14
    font_size_body = 11

    stream_parts: list[str] = []
    y = margin_top
    for i, line in enumerate(lines_to_render):
        if y < 50:
            # Simple page overflow — truncate (full pagination would need multi-page logic)
            stream_parts.append(f"BT /F1 {font_size_body} Tf {margin_x} {y} Td ([truncated...]) Tj ET")
            break
        if i == 0 and title:
            stream_parts.append(
                f"BT /F1 {font_size_title} Tf {margin_x} {y} Td ({_encode_text(line).decode('latin-1')}) Tj ET"
            )
            y -= line_height + 4
        elif line == "" and i == 1 and title:
            y -= 6
        else:
            stream_parts.append(
                f"BT /F1 {font_size_body} Tf {margin_x} {y} Td ({_encode_text(line).decode('latin-1')}) Tj ET"
            )
            y -= line_height

    stream_content = "\n".join(stream_parts)
    compressed = zlib.compress(stream_content.encode("latin-1"))

    # Build PDF objects
    objects: list[bytes] = []

    # Obj 1: Catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Obj 2: Pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [4 0 R] /Count 1 >>\nendobj\n")

    # Obj 3: Font
    objects.append(
        b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>\nendobj\n"
    )

    # Obj 5: Content stream
    stream_bytes = compressed
    obj5 = (
        f"5 0 obj\n<< /Length {len(stream_bytes)} /Filter /FlateDecode >>\nstream\n"
    ).encode("ascii") + stream_bytes + b"\nendstream\nendobj\n"
    objects.append(obj5)

    # Obj 4: Page
    objects.append((
        f"4 0 obj\n<< /Type /Page /Parent 2 0 R "
        f"/MediaBox [0 0 {page_width} {page_height}] "
        f"/Contents 5 0 R /Resources << /Font << /F1 3 0 R >> >> >>\nendobj\n"
    ).encode("ascii"))

    # Build cross-reference table
    header = b"%PDF-1.4\n"
    body_parts: list[bytes] = []
    offsets: list[int] = []
    pos = len(header)

    # Re-order objects 1-5 by their numbers
    ordered = [objects[0], objects[1], objects[2], objects[4], objects[3]]  # 1,2,3,4,5

    for obj_bytes in ordered:
        offsets.append(pos)
        body_parts.append(obj_bytes)
        pos += len(obj_bytes)

    xref_offset = pos
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode("ascii")

    trailer = f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")

    pdf_bytes = header + b"".join(body_parts) + xref + trailer
    out.write_bytes(pdf_bytes)
    return str(out)


# ---------------------------------------------------------------------------
# 11. pdf_to_images
# ---------------------------------------------------------------------------

def pdf_to_images(path: str, output_dir: str = "", dpi: int = 150) -> list[str]:
    """Convert PDF pages to image files. Returns list of image paths.

    Uses pdf2image (poppler) if available, else falls back to Ghostscript CLI.
    """
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR / f"pdf_images_{_ts()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem

    if _PDF2IMAGE:
        images = convert_from_path(str(pdf_path), dpi=dpi)
        paths: list[str] = []
        for i, img in enumerate(images, 1):
            img_path = out_dir / f"{stem}_page_{i:04d}.png"
            img.save(str(img_path), "PNG")
            paths.append(str(img_path))
        return paths

    # Ghostscript fallback
    out_pattern = str(out_dir / f"{stem}_page_%04d.png")
    try:
        subprocess.run(
            [
                "gs", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m",
                f"-r{dpi}", f"-sOutputFile={out_pattern}", str(pdf_path)
            ],
            check=True, timeout=120, capture_output=True
        )
        return sorted(str(p) for p in out_dir.glob(f"{stem}_page_*.png"))
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"pdf_to_images failed: pdf2image not installed and Ghostscript unavailable. {exc}")


# ---------------------------------------------------------------------------
# 12. compress_pdf
# ---------------------------------------------------------------------------

def compress_pdf(path: str, output_path: str = "") -> str:
    """Reduce PDF file size using Ghostscript. Returns path to compressed file.

    Falls back to pypdf writer (which reserialises and may reduce size slightly)
    if Ghostscript is not available.
    """
    in_path = Path(path)
    if not in_path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    out = _out_path(output_path, f"{in_path.stem}_compressed_{_ts()}.pdf")

    # Ghostscript compression (most effective)
    try:
        subprocess.run(
            [
                "gs", "-dNOPAUSE", "-dBATCH", "-dSAFER",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                "-dPDFSETTINGS=/ebook",
                f"-sOutputFile={out}",
                str(in_path),
            ],
            check=True, timeout=120, capture_output=True
        )
        return str(out)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # pypdf fallback — re-serialise to remove duplicate objects
    if _PYPDF:
        reader = pypdf.PdfReader(str(in_path))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        with open(str(out), "wb") as f:
            writer.write(f)
        return str(out)

    raise RuntimeError(
        "compress_pdf failed: Ghostscript not available and pypdf not installed."
    )
