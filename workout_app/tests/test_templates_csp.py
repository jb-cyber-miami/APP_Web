"""Templates must stay compatible with the strict CSP (no inline script/style/handlers)."""

import re
from pathlib import Path

import pytest

from app.security import CSP

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
FILES = sorted(TEMPLATES.rglob("*.html"))

EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: https://i.ytimg.com https://lh3.googleusercontent.com; "
    "frame-src https://www.youtube-nocookie.com; connect-src 'self'; font-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; manifest-src 'self'"
)


def test_templates_found():
    assert FILES


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_no_inline_script_style_or_handlers(path):
    text = path.read_text(encoding="utf-8")
    for tag in re.findall(r"<script\b[^>]*>", text, flags=re.IGNORECASE):
        assert "src=" in tag, f"inline <script> in {path.name}: {tag}"
    assert not re.search(r"<style\b", text, flags=re.IGNORECASE), f"<style> block in {path.name}"
    assert not re.search(r"\sstyle\s*=", text, flags=re.IGNORECASE), f'style="" in {path.name}'
    assert not re.search(r"\son[a-z]+\s*=", text, flags=re.IGNORECASE), f"on*= handler in {path.name}"
    assert "javascript:" not in text.lower(), f"javascript: URL in {path.name}"


def test_csp_unchanged():
    assert CSP == EXPECTED_CSP
    assert "unsafe-inline" not in CSP and "unsafe-eval" not in CSP


def test_csp_header_on_new_pages(alice):
    for path in ("/", "/history", "/exercises", "/settings"):
        assert alice.get(path).headers["Content-Security-Policy"] == EXPECTED_CSP
