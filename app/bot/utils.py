from __future__ import annotations

import html
import re
from collections.abc import Callable


_FENCE_RE = re.compile(r"^\s*```([^`]*)\s*$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-+*]\s+(.+)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def split_telegram_text(text: str, *, max_length: int = 4000) -> list[str]:
    if max_length < 100:
        raise ValueError("max_length is too small")
    if not text:
        return [""]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_length:
        window = remaining[:max_length]
        preferred_floor = max_length // 2
        cut = window.rfind("\n", preferred_floor)
        if cut <= 0:
            cut = window.rfind(" ", preferred_floor)
        if cut <= 0:
            cut = max_length

        chunk = remaining[:cut].rstrip()
        if not chunk:
            chunk = remaining[:max_length]
            cut = max_length
        chunks.append(chunk)
        remaining = remaining[cut:].lstrip("\n ")

    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def _allowed_link(url: str) -> bool:
    lowered = html.unescape(url).strip().lower()
    return lowered.startswith(("https://", "http://", "tg://", "mailto:"))


def _render_inline(text: str) -> str:
    """Render a conservative Markdown subset to Telegram-safe HTML.

    Model output is untrusted. We escape HTML first, then add only Telegram-supported tags.
    Unsupported Markdown is intentionally left as readable plain text instead of trying to
    implement a full CommonMark parser in the bot process.
    """

    escaped = html.escape(text, quote=False)
    placeholders: dict[str, str] = {}

    def stash(value: str) -> str:
        token = f"\x00TGPH{len(placeholders)}\x00"
        placeholders[token] = value
        return token

    def code_repl(match: re.Match[str]) -> str:
        return stash(f"<code>{match.group(1)}</code>")

    escaped = _INLINE_CODE_RE.sub(code_repl, escaped)

    def link_repl(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        if not _allowed_link(url):
            return f"{label} ({url})"
        return stash(f'<a href="{html.escape(html.unescape(url), quote=True)}">{label}</a>')

    escaped = _LINK_RE.sub(link_repl, escaped)

    # Strong emphasis first so the single-marker rules do not consume it.
    escaped = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<b>\1</b>", escaped)
    escaped = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"(?<!\w)_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)", r"<i>\1</i>", escaped)

    for token, value in placeholders.items():
        escaped = escaped.replace(token, value)
    return escaped


def _render_line(line: str) -> str:
    heading = _HEADING_RE.match(line)
    if heading:
        return f"<b>{_render_inline(heading.group(1))}</b>"

    quote = _QUOTE_RE.match(line)
    if quote:
        return f"<blockquote>{_render_inline(quote.group(1))}</blockquote>"

    bullet = _BULLET_RE.match(line)
    if bullet:
        indent = "  " * min(len(bullet.group(1)) // 2, 4)
        return f"{indent}• {_render_inline(bullet.group(2))}"

    ordered = _ORDERED_RE.match(line)
    if ordered:
        indent = "  " * min(len(ordered.group(1)) // 2, 4)
        return f"{indent}{ordered.group(2)}. {_render_inline(ordered.group(3))}"

    return _render_inline(line)


def _split_renderable(
    raw: str,
    renderer: Callable[[str], str],
    *,
    max_length: int,
) -> list[str]:
    """Split a single oversized raw unit while keeping every rendered fragment valid HTML."""
    if len(renderer(raw)) <= max_length:
        return [renderer(raw)]

    result: list[str] = []
    remaining = raw
    while remaining:
        if len(renderer(remaining)) <= max_length:
            result.append(renderer(remaining))
            break

        # Estimate a safe cut from rendered/raw expansion, then prefer a word boundary.
        rendered_len = max(1, len(renderer(remaining)))
        estimate = max(1, int(len(remaining) * (max_length / rendered_len) * 0.9))
        estimate = min(estimate, len(remaining))
        if estimate < 32 and len(remaining) > 32:
            estimate = 32

        cut = remaining.rfind(" ", 0, estimate + 1)
        if cut <= 0:
            cut = remaining.rfind("\n", 0, estimate + 1)
        if cut <= 0:
            cut = estimate

        piece = remaining[:cut].rstrip()
        if not piece:
            piece = remaining[: max(1, estimate)]
            cut = len(piece)

        # HTML escaping may expand a fragment more than estimated. Shrink until it fits.
        while piece and len(renderer(piece)) > max_length:
            piece = piece[: max(1, len(piece) // 2)].rstrip()
            cut = len(piece)

        result.append(renderer(piece))
        remaining = remaining[cut:].lstrip()
    return result


def telegram_markdown_to_html_chunks(text: str, *, max_length: int = 3900) -> list[str]:
    """Convert common model Markdown to safe Telegram HTML chunks.

    Supported output includes headings, bold/italic/strike, inline code, fenced code blocks,
    blockquotes, links, bullets and numbered lists. Every returned chunk is independently valid
    HTML, so Telegram can parse it safely even when a long answer is split across messages.
    """
    if max_length < 500:
        raise ValueError("max_length is too small")
    if not text:
        return [""]

    units: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0

    while index < len(lines):
        line = lines[index]
        fence = _FENCE_RE.match(line)
        if fence:
            language = re.sub(r"[^A-Za-z0-9_+.-]", "", fence.group(1).strip())[:32]
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1  # closing fence

            code = "\n".join(code_lines)
            class_attr = f' class="language-{html.escape(language, quote=True)}"' if language else ""

            def render_code(piece: str, attr: str = class_attr) -> str:
                return f"<pre><code{attr}>{html.escape(piece, quote=False)}</code></pre>"

            units.extend(_split_renderable(code, render_code, max_length=max_length))
            continue

        if line == "":
            units.append("")
            index += 1
            continue

        # A normal model line is usually far below Telegram's limit. For pathological long lines,
        # split the raw text first and render each fragment independently.
        units.extend(_split_renderable(line, _render_line, max_length=max_length))
        index += 1

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else f"{current}\n{unit}"
        if len(candidate) <= max_length:
            current = candidate
            continue

        if current:
            chunks.append(current.rstrip())
        current = unit

    if current or not chunks:
        chunks.append(current.rstrip())
    return chunks
