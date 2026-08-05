"""Shared regex helpers for MistfallDB's page markup.

All content lives in server-rendered HTML (not JSON), but the site uses a
consistent set of patterns across page types:
  - <meta name="description" content="..."> for a one-line summary
  - <h1>...</h1> for the page title
  - <dt class="text-muted-foreground">Label</dt><dd ...>Value</dd> for stat rows
"""
from __future__ import annotations

import html
import re

_META_DESC_RE = re.compile(r'<meta name="description" content="([^"]*)"')
_H1_RE = re.compile(r"<h1[^>]*>([^<]*)</h1>")
_DT_DD_RE = re.compile(
    r'<dt class="text-muted-foreground">([^<]+)</dt><dd[^>]*>(.*?)</dd>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s)).strip()


def meta_description(page_html: str) -> str | None:
    m = _META_DESC_RE.search(page_html)
    return html.unescape(m.group(1)) if m else None


def h1_title(page_html: str) -> str | None:
    m = _H1_RE.search(page_html)
    return html.unescape(m.group(1)).strip() if m else None


def dt_dd_pairs(page_html: str) -> dict[str, str]:
    return {
        html.unescape(k).strip(): strip_tags(v)
        for k, v in _DT_DD_RE.findall(page_html)
    }
