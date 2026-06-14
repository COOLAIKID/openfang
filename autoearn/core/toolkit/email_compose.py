"""Email composition and sending toolkit.

All public functions return str (HTML, JSON, or plain text).
Config is read from [smtp], [sendgrid], and [mailgun] sections of config.toml.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_module
import json
import os
import re
import smtplib
import socket
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any

import requests

from ..config import get, section

# ---------------------------------------------------------------------------
# Colour palette (inline CSS — email clients require this)
# ---------------------------------------------------------------------------

_C = {
    "primary":      "#2563EB",
    "primary_dark": "#1D4ED8",
    "success":      "#16A34A",
    "warning":      "#D97706",
    "danger":       "#DC2626",
    "info":         "#0891B2",
    "text":         "#1F2937",
    "muted":        "#6B7280",
    "border":       "#E5E7EB",
    "bg":           "#F9FAFB",
    "white":        "#FFFFFF",
}

_SEVERITY_COLORS = {
    "info":     _C["info"],
    "success":  _C["success"],
    "warning":  _C["warning"],
    "error":    _C["danger"],
    "critical": "#7C3AED",
}

# ---------------------------------------------------------------------------
# SMTP / API config helpers
# ---------------------------------------------------------------------------


def _smtp_cfg(key: str, default: str = "") -> str:
    return get("smtp", key, "") or os.environ.get(f"SMTP_{key.upper()}", default)


def _sendgrid_key() -> str:
    return get("sendgrid", "api_key", "") or os.environ.get("SENDGRID_API_KEY", "")


def _sendgrid_from() -> str:
    return get("sendgrid", "from_email", "") or os.environ.get("SENDGRID_FROM", "")


def _mailgun_key() -> str:
    return get("mailgun", "api_key", "") or os.environ.get("MAILGUN_API_KEY", "")


def _mailgun_domain() -> str:
    return get("mailgun", "domain", "") or os.environ.get("MAILGUN_DOMAIN", "")


# ---------------------------------------------------------------------------
# Internal: base HTML wrapper
# ---------------------------------------------------------------------------


def _base_template(title: str, body_html: str, unsubscribe_url: str = "") -> str:
    """Wrap body_html in a full, responsive HTML email shell."""
    unsub_block = ""
    if unsubscribe_url:
        ue = html_module.escape(unsubscribe_url)
        unsub_block = (
            f'<p style="margin:0;font-size:12px;color:{_C["muted"]};text-align:center;">'
            f'<a href="{ue}" style="color:{_C["muted"]};">Unsubscribe</a></p>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{html_module.escape(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:{_C['bg']};font-family:Arial,Helvetica,sans-serif;color:{_C['text']};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background-color:{_C['bg']};padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0"
               style="max-width:600px;width:100%;background-color:{_C['white']};
                      border-radius:8px;overflow:hidden;
                      border:1px solid {_C['border']};">
          <tr>
            <td style="padding:32px 40px;">
              {body_html}
            </td>
          </tr>
          <tr>
            <td style="padding:16px 40px 24px;border-top:1px solid {_C['border']};
                       background-color:{_C['bg']};">
              {unsub_block}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 1. markdown_to_email_html
# ---------------------------------------------------------------------------


def markdown_to_email_html(markdown_text: str) -> str:
    """
    Convert a subset of Markdown to email-safe HTML with inline CSS styles.

    Handles: h1-h4, **bold**, *italic*, [links](url), unordered/ordered lists,
    `inline code`, fenced code blocks, horizontal rules (---), blockquotes, paragraphs.
    """
    lines = markdown_text.split("\n")
    output: list[str] = []
    in_ul = False
    in_ol = False
    in_code_block = False
    code_lines: list[str] = []

    def close_lists() -> list[str]:
        nonlocal in_ul, in_ol
        parts = []
        if in_ul:
            parts.append("</ul>")
            in_ul = False
        if in_ol:
            parts.append("</ol>")
            in_ol = False
        return parts

    def inline(text: str) -> str:
        # Bold + italic combined
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
        # Bold
        text = re.sub(r"\*\*(.+?)\*\*",
                      r'<strong style="font-weight:700;">\1</strong>', text)
        text = re.sub(r"__(.+?)__",
                      r'<strong style="font-weight:700;">\1</strong>', text)
        # Italic
        text = re.sub(r"\*(.+?)\*",
                      r'<em style="font-style:italic;">\1</em>', text)
        text = re.sub(r"_(.+?)_",
                      r'<em style="font-style:italic;">\1</em>', text)
        # Inline code
        text = re.sub(
            r"`(.+?)`",
            r'<code style="font-family:monospace;background:#F3F4F6;padding:2px 5px;'
            r'border-radius:3px;font-size:13px;">\1</code>',
            text,
        )
        # Links
        text = re.sub(
            r"\[(.+?)\]\((.+?)\)",
            lambda m: (
                f'<a href="{html_module.escape(m.group(2))}" '
                f'style="color:{_C["primary"]};text-decoration:underline;">'
                f"{m.group(1)}</a>"
            ),
            text,
        )
        return text

    for line in lines:
        # Fenced code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                code_html = html_module.escape("\n".join(code_lines))
                output.append(
                    f'<pre style="background:#1F2937;color:#F9FAFB;padding:16px;'
                    f'border-radius:6px;overflow-x:auto;font-family:monospace;'
                    f'font-size:13px;line-height:1.5;">'
                    f"<code>{code_html}</code></pre>"
                )
                code_lines = []
                in_code_block = False
            else:
                output.extend(close_lists())
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Headings h1-h4
        if line.startswith("#### "):
            output.extend(close_lists())
            output.append(
                f'<h4 style="font-size:15px;font-weight:700;margin:20px 0 6px;'
                f'color:{_C["text"]};">{inline(line[5:])}</h4>'
            )
            continue
        if line.startswith("### "):
            output.extend(close_lists())
            output.append(
                f'<h3 style="font-size:18px;font-weight:700;margin:24px 0 8px;'
                f'color:{_C["text"]};">{inline(line[4:])}</h3>'
            )
            continue
        if line.startswith("## "):
            output.extend(close_lists())
            output.append(
                f'<h2 style="font-size:22px;font-weight:700;margin:28px 0 10px;'
                f'color:{_C["text"]};">{inline(line[3:])}</h2>'
            )
            continue
        if line.startswith("# "):
            output.extend(close_lists())
            output.append(
                f'<h1 style="font-size:28px;font-weight:700;margin:0 0 16px;'
                f'color:{_C["text"]};">{inline(line[2:])}</h1>'
            )
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}$", line.strip()):
            output.extend(close_lists())
            output.append(
                f'<hr style="border:none;border-top:1px solid {_C["border"]};margin:24px 0;">'
            )
            continue

        # Blockquote
        if line.startswith("> "):
            output.extend(close_lists())
            output.append(
                f'<blockquote style="margin:16px 0;padding:12px 16px;'
                f'border-left:4px solid {_C["primary"]};background:#EFF6FF;'
                f'color:{_C["muted"]};font-style:italic;">{inline(line[2:])}</blockquote>'
            )
            continue

        # Unordered list
        ul_match = re.match(r"^[-*+] (.+)$", line)
        if ul_match:
            if not in_ul:
                output.extend(close_lists())
                output.append('<ul style="margin:8px 0;padding-left:24px;">')
                in_ul = True
            output.append(f'<li style="margin:4px 0;">{inline(ul_match.group(1))}</li>')
            continue

        # Ordered list
        ol_match = re.match(r"^\d+\. (.+)$", line)
        if ol_match:
            if not in_ol:
                output.extend(close_lists())
                output.append('<ol style="margin:8px 0;padding-left:24px;">')
                in_ol = True
            output.append(f'<li style="margin:4px 0;">{inline(ol_match.group(1))}</li>')
            continue

        # Blank line — close lists and add spacing
        if line.strip() == "":
            output.extend(close_lists())
            output.append("<br>")
            continue

        # Regular paragraph
        output.extend(close_lists())
        output.append(
            f'<p style="margin:0 0 12px;line-height:1.6;font-size:15px;">{inline(line)}</p>'
        )

    output.extend(close_lists())
    return "\n".join(output)


# ---------------------------------------------------------------------------
# 2. email_template_newsletter
# ---------------------------------------------------------------------------


def email_template_newsletter(
    company_name: str,
    headline: str,
    sections: list[dict[str, Any]],
    footer_text: str = "",
) -> str:
    """
    Build a newsletter HTML email.

    sections = [{title, content, image_url?}].
    Returns full HTML string.
    """
    sections_html = ""
    for sec in sections:
        title = html_module.escape(sec.get("title", ""))
        content = sec.get("content", "")
        image_url = sec.get("image_url", "")

        img_block = ""
        if image_url:
            iu = html_module.escape(image_url)
            img_block = (
                f'<img src="{iu}" alt="{title}" '
                f'style="width:100%;max-width:520px;height:auto;display:block;'
                f'margin:0 auto 16px;border-radius:6px;">'
            )

        cta_html = ""
        if sec.get("cta_text") and sec.get("cta_url"):
            cu = html_module.escape(sec["cta_url"])
            ct = html_module.escape(sec["cta_text"])
            cta_html = (
                f'<div style="margin:16px 0;">'
                f'<a href="{cu}" style="display:inline-block;background-color:{_C["success"]};'
                f'color:#fff;text-decoration:none;padding:10px 22px;border-radius:5px;'
                f'font-weight:700;font-size:14px;">{ct}</a></div>'
            )

        sections_html += f"""
<div style="margin-bottom:32px;">
  {img_block}
  <h2 style="font-size:20px;font-weight:700;color:{_C['text']};margin:0 0 10px;">{title}</h2>
  <div style="font-size:15px;line-height:1.7;color:{_C['text']};">
    {markdown_to_email_html(content)}
  </div>
  {cta_html}
  <hr style="border:none;border-top:1px solid {_C['border']};margin:24px 0 0;">
</div>"""

    footer = html_module.escape(footer_text or f"© {company_name}. All rights reserved.")
    body = f"""
<div style="background-color:{_C['primary']};padding:24px 40px;margin:-32px -40px 32px;border-radius:8px 8px 0 0;">
  <h1 style="margin:0;font-size:24px;color:#fff;font-weight:700;">{html_module.escape(company_name)}</h1>
</div>
<h1 style="font-size:26px;font-weight:800;color:{_C['text']};margin:0 0 24px;">
  {html_module.escape(headline)}
</h1>
{sections_html}
<p style="font-size:13px;color:{_C['muted']};text-align:center;margin:8px 0 0;">{footer}</p>"""

    return _base_template(headline, body)


# ---------------------------------------------------------------------------
# 3. email_template_welcome
# ---------------------------------------------------------------------------


def email_template_welcome(
    name: str,
    product_name: str,
    login_url: str,
    support_email: str,
) -> str:
    """
    Welcome email with name greeting, product overview, login button, support link.

    Returns full HTML string.
    """
    lu = html_module.escape(login_url)
    se = html_module.escape(support_email)
    body = f"""
<div style="text-align:center;padding:8px 0 32px;">
  <h1 style="font-size:26px;font-weight:800;color:{_C['text']};margin:16px 0 8px;">
    Welcome to {html_module.escape(product_name)}, {html_module.escape(name)}!
  </h1>
  <p style="font-size:15px;color:{_C['muted']};margin:0 0 28px;line-height:1.6;">
    We're thrilled to have you on board. Your account is ready to go.
  </p>
  <a href="{lu}"
     style="display:inline-block;background-color:{_C['primary']};
            color:#fff;text-decoration:none;padding:14px 32px;
            border-radius:6px;font-weight:700;font-size:15px;">
    Get Started
  </a>
</div>

<hr style="border:none;border-top:1px solid {_C['border']};margin:24px 0;">

<h3 style="font-size:16px;font-weight:700;color:{_C['text']};margin:0 0 12px;">What's next?</h3>
<ul style="padding-left:20px;margin:0 0 24px;">
  <li style="margin:6px 0;font-size:14px;color:{_C['text']};">Complete your profile</li>
  <li style="margin:6px 0;font-size:14px;color:{_C['text']};">Explore the dashboard</li>
  <li style="margin:6px 0;font-size:14px;color:{_C['text']};">Invite your team</li>
</ul>

<p style="font-size:14px;color:{_C['muted']};margin:0;">
  Need help? Reply to this email or contact us at
  <a href="mailto:{se}" style="color:{_C['primary']};">{se}</a>.
</p>"""

    return _base_template(f"Welcome to {product_name}!", body)


# ---------------------------------------------------------------------------
# 4. email_template_invoice
# ---------------------------------------------------------------------------


def email_template_invoice(
    items: list[dict[str, Any]],
    subtotal: float,
    tax: float,
    total: float,
    due_date: str,
    company_info: dict[str, Any],
    customer_info: dict[str, Any],
) -> str:
    """
    Professional invoice HTML email.

    items = [{description, qty, unit_price, line_total}]
    company_info / customer_info = {name, address, email}
    Returns full HTML string.
    """
    rows_html = ""
    for item in items:
        desc = html_module.escape(str(item.get("description", "")))
        qty = html_module.escape(str(item.get("qty", item.get("quantity", "1"))))
        try:
            up = float(item.get("unit_price", 0))
        except (ValueError, TypeError):
            up = 0.0
        try:
            lt = float(item.get("line_total", item.get("amount", up)))
        except (ValueError, TypeError):
            lt = 0.0

        rows_html += f"""
<tr>
  <td style="padding:10px 8px;border-bottom:1px solid {_C['border']};font-size:14px;">{desc}</td>
  <td style="padding:10px 8px;border-bottom:1px solid {_C['border']};font-size:14px;text-align:center;">{qty}</td>
  <td style="padding:10px 8px;border-bottom:1px solid {_C['border']};font-size:14px;text-align:right;">${up:.2f}</td>
  <td style="padding:10px 8px;border-bottom:1px solid {_C['border']};font-size:14px;text-align:right;">${lt:.2f}</td>
</tr>"""

    co = company_info
    cu = customer_info
    body = f"""
<div style="display:flex;justify-content:space-between;margin-bottom:24px;">
  <div>
    <h2 style="font-size:22px;font-weight:800;color:{_C['primary']};margin:0 0 4px;">INVOICE</h2>
    <p style="margin:0;font-size:13px;color:{_C['muted']};">Due: {html_module.escape(due_date)}</p>
  </div>
  <div style="text-align:right;">
    <p style="margin:0;font-weight:700;font-size:15px;">{html_module.escape(co.get('name',''))}</p>
    <p style="margin:0;font-size:13px;color:{_C['muted']};">{html_module.escape(co.get('address',''))}</p>
    <p style="margin:0;font-size:13px;color:{_C['muted']};">{html_module.escape(co.get('email',''))}</p>
  </div>
</div>

<div style="background:{_C['bg']};padding:16px;border-radius:6px;margin-bottom:24px;">
  <p style="margin:0 0 4px;font-size:12px;color:{_C['muted']};text-transform:uppercase;">Bill To</p>
  <p style="margin:0;font-weight:700;">{html_module.escape(cu.get('name',''))}</p>
  <p style="margin:0;font-size:13px;color:{_C['muted']};">{html_module.escape(cu.get('address',''))}</p>
  <p style="margin:0;font-size:13px;color:{_C['muted']};">{html_module.escape(cu.get('email',''))}</p>
</div>

<table width="100%" cellpadding="0" cellspacing="0">
  <thead>
    <tr style="background:{_C['primary']};">
      <th style="padding:10px 8px;text-align:left;font-size:13px;color:#fff;">Description</th>
      <th style="padding:10px 8px;text-align:center;font-size:13px;color:#fff;">Qty</th>
      <th style="padding:10px 8px;text-align:right;font-size:13px;color:#fff;">Unit Price</th>
      <th style="padding:10px 8px;text-align:right;font-size:13px;color:#fff;">Total</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>

<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
  <tr>
    <td width="60%"></td>
    <td style="padding:6px 8px;font-size:14px;color:{_C['muted']};">Subtotal</td>
    <td style="padding:6px 8px;font-size:14px;text-align:right;">${subtotal:.2f}</td>
  </tr>
  <tr>
    <td></td>
    <td style="padding:6px 8px;font-size:14px;color:{_C['muted']};">Tax</td>
    <td style="padding:6px 8px;font-size:14px;text-align:right;">${tax:.2f}</td>
  </tr>
  <tr style="background:{_C['bg']};">
    <td></td>
    <td style="padding:10px 8px;font-size:16px;font-weight:800;">Total</td>
    <td style="padding:10px 8px;font-size:16px;font-weight:800;text-align:right;
               color:{_C['primary']};">${total:.2f}</td>
  </tr>
</table>"""

    return _base_template("Invoice", body)


# ---------------------------------------------------------------------------
# 5. email_template_alert
# ---------------------------------------------------------------------------


def email_template_alert(
    title: str,
    message: str,
    severity: str = "info",
    cta_text: str = "",
    cta_url: str = "",
) -> str:
    """
    Alert/notification email.

    severity: info (blue) / warning (orange) / error (red) / success (green).
    Returns full HTML string.
    """
    color = _SEVERITY_COLORS.get(severity, _SEVERITY_COLORS["info"])
    icon_map = {
        "info": "ℹ️", "success": "✅", "warning": "⚠️",
        "error": "❌", "critical": "🚨",
    }
    icon = icon_map.get(severity, "ℹ️")

    cta_block = ""
    if cta_text and cta_url:
        cu = html_module.escape(cta_url)
        ct = html_module.escape(cta_text)
        cta_block = (
            f'<div style="text-align:center;margin-top:24px;">'
            f'<a href="{cu}" style="display:inline-block;background-color:{color};'
            f'color:#fff;text-decoration:none;padding:12px 28px;border-radius:6px;'
            f'font-weight:700;">{ct}</a></div>'
        )

    body = f"""
<div style="border-left:4px solid {color};padding:16px 20px;
            background:{color}18;border-radius:0 6px 6px 0;margin-bottom:24px;">
  <h2 style="margin:0 0 8px;font-size:18px;font-weight:700;color:{color};">
    {icon} {html_module.escape(title)}
  </h2>
  <p style="margin:0;font-size:15px;line-height:1.6;color:{_C['text']};">
    {html_module.escape(message)}
  </p>
</div>
{cta_block}"""

    return _base_template(title, body)


# ---------------------------------------------------------------------------
# 6. email_template_digest
# ---------------------------------------------------------------------------


def email_template_digest(
    title: str,
    items: list[dict[str, Any]],
    period: str = "weekly",
) -> str:
    """
    Digest/roundup email with card-based layout.

    items = [{title, summary, url, category}].
    Returns full HTML string.
    """
    period_label = period.capitalize()
    cards_html = ""
    category_colors: dict[str, str] = {
        "news":        "#2563EB",
        "technology":  "#7C3AED",
        "finance":     "#16A34A",
        "health":      "#0891B2",
        "sports":      "#EA580C",
        "entertainment": "#DB2777",
    }

    for item in items:
        item_title = html_module.escape(item.get("title", "Untitled"))
        summary = html_module.escape(item.get("summary", ""))
        url = html_module.escape(item.get("url", "#"))
        category = item.get("category", "")
        cat_color = category_colors.get(category.lower(), _C["primary"])
        cat_label = html_module.escape(category.upper()) if category else ""

        cat_badge = ""
        if cat_label:
            cat_badge = (
                f'<span style="display:inline-block;background:{cat_color};color:#fff;'
                f'font-size:10px;padding:2px 8px;border-radius:20px;font-weight:700;'
                f'text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">'
                f"{cat_label}</span>"
            )

        cards_html += f"""
<div style="border:1px solid {_C['border']};border-radius:6px;padding:16px 20px;
            margin-bottom:16px;background:{_C['white']};">
  {cat_badge}
  <h3 style="margin:0 0 6px;font-size:16px;font-weight:700;color:{_C['text']};">
    <a href="{url}" style="color:{_C['text']};text-decoration:none;">{item_title}</a>
  </h3>
  <p style="margin:0 0 10px;font-size:14px;color:{_C['muted']};line-height:1.5;">{summary}</p>
  <a href="{url}" style="font-size:13px;color:{_C['primary']};font-weight:600;
     text-decoration:none;">Read more &rarr;</a>
</div>"""

    body = f"""
<div style="background:{_C['primary']};padding:20px 40px;margin:-32px -40px 28px;border-radius:8px 8px 0 0;">
  <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.7);text-transform:uppercase;
     letter-spacing:.08em;">{period_label} Digest</p>
  <h1 style="margin:4px 0 0;font-size:22px;color:#fff;font-weight:700;">
    {html_module.escape(title)}
  </h1>
</div>
<p style="font-size:13px;color:{_C['muted']};margin:0 0 20px;">
  {len(items)} item{"s" if len(items) != 1 else ""} curated for you
</p>
{cards_html}"""

    return _base_template(title, body)


# ---------------------------------------------------------------------------
# 7. compose_html_email
# ---------------------------------------------------------------------------


def compose_html_email(
    subject: str,
    body_markdown: str,
    from_name: str,
    cta_text: str = "",
    cta_url: str = "",
    unsubscribe_url: str = "",
) -> str:
    """
    All-in-one email composer: markdown → HTML → complete email template.

    Returns JSON {subject, html, text_plain}.
    """
    body_html = markdown_to_email_html(body_markdown)

    # From-name header block
    header_html = (
        f'<div style="margin-bottom:24px;">'
        f'<p style="margin:0;font-size:13px;color:{_C["muted"]};">From: '
        f'<strong>{html_module.escape(from_name)}</strong></p>'
        f'<hr style="border:none;border-top:1px solid {_C["border"]};margin:10px 0 0;">'
        f'</div>'
    )

    full_body = header_html + body_html

    if cta_text and cta_url:
        cu = html_module.escape(cta_url)
        ct = html_module.escape(cta_text)
        full_body += (
            f'<div style="text-align:center;margin:28px 0;">'
            f'<a href="{cu}" style="display:inline-block;background-color:{_C["primary"]};'
            f'color:#fff;text-decoration:none;padding:12px 28px;border-radius:6px;'
            f'font-weight:700;font-size:15px;">{ct}</a></div>'
        )

    html_out = _base_template(subject, full_body, unsubscribe_url)

    # Plain-text fallback
    text_plain = re.sub(r"<[^>]+>", "", body_markdown)
    if cta_text and cta_url:
        text_plain += f"\n\n{cta_text}: {cta_url}"
    if unsubscribe_url:
        text_plain += f"\n\nUnsubscribe: {unsubscribe_url}"

    return json.dumps({
        "subject": subject,
        "html": html_out,
        "text_plain": text_plain.strip(),
    })


# ---------------------------------------------------------------------------
# 8. send_smtp
# ---------------------------------------------------------------------------


def send_smtp(
    to: str | list[str],
    subject: str,
    html_body: str,
    text_body: str = "",
    from_email: str = "",
    from_name: str = "",
    cc: list[str] = [],
    attachments: list[dict[str, Any]] = [],
) -> str:
    """
    Send an email via SMTP.

    Config: [smtp] host, port, username, password, use_tls.
    attachments = [{filename, data: bytes, mimetype}].
    Returns JSON {sent, message_id, error}.
    """
    host = _smtp_cfg("host", "localhost")
    try:
        port = int(_smtp_cfg("port", "587"))
    except ValueError:
        port = 587
    username = _smtp_cfg("username", "")
    password = _smtp_cfg("password", "")
    use_tls_str = _smtp_cfg("use_tls", "true")
    use_tls = use_tls_str.lower() not in ("false", "0", "no")

    sender_email = from_email or _smtp_cfg("from_email", "noreply@example.com")
    sender_name = from_name or _smtp_cfg("from_name", "AutoEarn")
    recipients = [to] if isinstance(to, str) else list(to)

    if attachments:
        msg: MIMEMultipart = MIMEMultipart("mixed")
        alt = MIMEMultipart("alternative")
    else:
        msg = MIMEMultipart("alternative")
        alt = msg

    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = ", ".join(recipients)
    if cc:
        msg["Cc"] = ", ".join(cc)
    message_id = make_msgid()
    msg["Message-ID"] = message_id

    if text_body:
        alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))

    if attachments:
        msg.attach(alt)
        for att in attachments:
            part = MIMEApplication(att.get("data", b""), Name=att.get("filename", "file"))
            part["Content-Disposition"] = (
                f'attachment; filename="{att.get("filename", "file")}"'
            )
            msg.attach(part)

    all_recipients = recipients + list(cc)

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            if username and password:
                server.login(username, password)
            server.sendmail(sender_email, all_recipients, msg.as_string())
        return json.dumps({"sent": True, "message_id": message_id, "error": ""})
    except Exception as exc:
        return json.dumps({"sent": False, "message_id": "", "error": str(exc)})


# ---------------------------------------------------------------------------
# 9. send_sendgrid
# ---------------------------------------------------------------------------


def send_sendgrid(
    to: str | list[str],
    subject: str,
    html_body: str,
    text_body: str = "",
    from_email: str = "",
) -> str:
    """
    Send an email via SendGrid API.

    Config: [sendgrid] api_key, from_email.
    Returns JSON {sent, status_code, error}.
    """
    api_key = _sendgrid_key()
    if not api_key:
        return json.dumps({"sent": False, "status_code": 0,
                           "error": "[sendgrid] api_key not configured"})

    sender = from_email or _sendgrid_from() or "noreply@example.com"
    recipients = [to] if isinstance(to, str) else list(to)

    payload: dict[str, Any] = {
        "personalizations": [
            {"to": [{"email": addr} for addr in recipients], "subject": subject}
        ],
        "from": {"email": sender},
        "subject": subject,
        "content": [],
    }
    if text_body:
        payload["content"].append({"type": "text/plain", "value": text_body})
    payload["content"].append({"type": "text/html", "value": html_body})

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        message_id = resp.headers.get("X-Message-Id", "")
        if resp.status_code in (200, 202):
            return json.dumps({"sent": True, "status_code": resp.status_code, "error": ""})
        return json.dumps({
            "sent": False,
            "status_code": resp.status_code,
            "error": resp.text[:300],
        })
    except Exception as exc:
        return json.dumps({"sent": False, "status_code": 0, "error": str(exc)})


# ---------------------------------------------------------------------------
# 10. send_mailgun
# ---------------------------------------------------------------------------


def send_mailgun(
    to: str | list[str],
    subject: str,
    html_body: str,
    text_body: str = "",
    domain: str = "",
    from_email: str = "",
) -> str:
    """
    Send an email via Mailgun API.

    Config: [mailgun] api_key, domain.
    Returns JSON {sent, id, error}.
    """
    api_key = _mailgun_key()
    mg_domain = domain or _mailgun_domain()

    if not api_key:
        return json.dumps({"sent": False, "id": "", "error": "[mailgun] api_key not configured"})
    if not mg_domain:
        return json.dumps({"sent": False, "id": "", "error": "[mailgun] domain not configured"})

    recipients = [to] if isinstance(to, str) else list(to)
    sender = from_email or f"noreply@{mg_domain}"

    data: dict[str, Any] = {
        "from": sender,
        "to": recipients,
        "subject": subject,
        "html": html_body,
    }
    if text_body:
        data["text"] = text_body

    try:
        resp = requests.post(
            f"https://api.mailgun.net/v3/{mg_domain}/messages",
            auth=("api", api_key),
            data=data,
            timeout=20,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code == 200:
            return json.dumps({"sent": True, "id": body.get("id", ""), "error": ""})
        return json.dumps({"sent": False, "id": "",
                           "error": body.get("message", resp.text[:200])})
    except Exception as exc:
        return json.dumps({"sent": False, "id": "", "error": str(exc)})


# ---------------------------------------------------------------------------
# 11. validate_email
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9._%+\-]*[a-zA-Z0-9])?@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

_DISPOSABLE_DOMAINS: frozenset[str] = frozenset([
    "mailinator.com", "guerrillamail.com", "trashmail.com", "yopmail.com",
    "tempmail.com", "throwaway.email", "sharklasers.com", "spam4.me",
    "maildrop.cc", "dispostable.com", "getairmail.com", "fakeinbox.com",
])


def validate_email(address: str) -> str:
    """
    Validate an email address via RFC 5322 regex + optional MX socket check.

    Returns JSON {valid, has_mx, error}.
    """
    address = address.strip()
    if not _EMAIL_RE.match(address):
        return json.dumps({"valid": False, "has_mx": False, "error": "Invalid format"})

    domain = address.split("@", 1)[1].lower()

    if domain in _DISPOSABLE_DOMAINS:
        return json.dumps({"valid": False, "has_mx": False, "error": "Disposable email domain"})

    # Light MX check via socket
    has_mx = False
    error = ""
    try:
        socket.setdefaulttimeout(5)
        results = socket.getaddrinfo(domain, None)
        has_mx = bool(results)
    except socket.gaierror as exc:
        error = f"Domain does not resolve: {exc}"
    except Exception as exc:
        error = str(exc)

    valid = has_mx or (not error)
    return json.dumps({"valid": valid, "has_mx": has_mx, "error": error})


# ---------------------------------------------------------------------------
# 12. generate_unsubscribe_token
# ---------------------------------------------------------------------------


def generate_unsubscribe_token(email: str, secret: str) -> str:
    """
    Generate a URL-safe HMAC-SHA256 token for unsubscribe links.

    Returns the hex digest token string.
    """
    key = secret.encode("utf-8")
    msg = email.lower().strip().encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return digest


# ---------------------------------------------------------------------------
# 13. parse_email_headers
# ---------------------------------------------------------------------------


def parse_email_headers(raw_headers: str) -> str:
    """
    Parse "Key: Value\\n" raw email headers into a JSON dict.

    Handles folded (multi-line) headers per RFC 2822.
    """
    # Unfold continuation lines
    unfolded = re.sub(r"\r?\n[ \t]+", " ", raw_headers)
    result: dict[str, str] = {}
    for line in unfolded.splitlines():
        if not line.strip():
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key not in result:
                result[key] = value
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 14. extract_email_links
# ---------------------------------------------------------------------------

_RE_HREF = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_RE_STRIP_TAGS = re.compile(r"<[^>]+>")


def extract_email_links(html: str) -> str:
    """
    Extract all href values from <a> tags via regex.

    Returns JSON list of href URL strings.
    """
    hrefs: list[str] = []
    seen: set[str] = set()
    for match in _RE_HREF.finditer(html):
        href = match.group(1).strip()
        if href and href not in seen:
            seen.add(href)
            hrefs.append(href)
    return json.dumps(hrefs)


# ---------------------------------------------------------------------------
# 15. personalize_template
# ---------------------------------------------------------------------------


def personalize_template(template_html: str, variables: dict[str, Any]) -> str:
    """
    Replace {{variable_name}} placeholders in template_html with values from variables dict.

    Returns the personalized HTML string.
    """
    result = template_html
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(value))
    # Also replace any remaining unmatched placeholders with empty string
    result = re.sub(r"\{\{[^}]+\}\}", "", result)
    return result
