"""
formatter.py — Convert AI markdown responses to Telegram HTML.
Handles: code blocks, inline code, bold, italic, escape HTML safely.
"""

import re
import html as _html

# ── Supported language aliases (for display label) ────────────────────────────
_LANG_LABELS = {
    "python": "Python", "py": "Python",
    "javascript": "JavaScript", "js": "JavaScript",
    "typescript": "TypeScript", "ts": "TypeScript",
    "bash": "Bash", "sh": "Bash", "shell": "Bash",
    "json": "JSON",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
    "yaml": "YAML", "yml": "YAML",
    "xml": "XML",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
    "c": "C", "cpp": "C++", "c++": "C++",
    "php": "PHP",
    "ruby": "Ruby", "rb": "Ruby",
    "dockerfile": "Dockerfile",
    "toml": "TOML",
    "ini": "INI",
    "nginx": "Nginx",
    "log": "Log",
    "text": "Text", "txt": "Text",
}

# ── Code block regex: ```lang\n...\n``` ───────────────────────────────────────
_CODE_BLOCK_RE = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)

# ── Inline code: `text` (single backtick, no newlines) ───────────────────────
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

# ── Bold: **text** ────────────────────────────────────────────────────────────
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)

# ── Italic: *text* (not double) ───────────────────────────────────────────────
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")

# ── Italic: _text_ ────────────────────────────────────────────────────────────
_ITALIC2_RE = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")

# ── Headers: # H1 / ## H2 → bold ─────────────────────────────────────────────
_HEADER_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _escape(text: str) -> str:
    """HTML-escape: & → &amp;  < → &lt;  > → &gt;"""
    return _html.escape(text, quote=False)


def _format_text_segment(text: str) -> str:
    """
    Apply inline formatting to a plain-text segment (no code blocks).
    Order matters: escape first, then apply Telegram markup.
    """
    text = _escape(text)

    # Headers → bold line
    text = _HEADER_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)

    # Bold
    text = _BOLD_RE.sub(r"<b>\1</b>", text)

    # Italic (*text*)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)

    # Italic (_text_)
    text = _ITALIC2_RE.sub(r"<i>\1</i>", text)

    # Inline code — content already escaped by _escape above
    text = _INLINE_CODE_RE.sub(r"<code>\1</code>", text)

    return text


def format_telegram(text: str) -> str:
    """
    Convert an AI markdown response to Telegram-safe HTML.

    - ``` code blocks ``` → <pre>...</pre>
    - `inline code`       → <code>...</code>
    - **bold**            → <b>...</b>
    - *italic* / _italic_ → <i>...</i>
    - # Headers           → <b>...</b>
    - All other text HTML-escaped safely
    """
    if not text:
        return ""

    parts = []
    pos   = 0

    for m in _CODE_BLOCK_RE.finditer(text):
        # Text before this code block
        before = text[pos : m.start()]
        if before:
            parts.append(_format_text_segment(before))

        lang_raw   = m.group(1).strip().lower()
        code_raw   = m.group(2)
        code_clean = _escape(code_raw.strip())

        label = _LANG_LABELS.get(lang_raw, lang_raw.title() if lang_raw else None)

        if label:
            parts.append(f"<b>{_escape(label)}</b>\n<pre>{code_clean}</pre>")
        else:
            parts.append(f"<pre>{code_clean}</pre>")

        pos = m.end()

    # Remaining text after last code block
    remainder = text[pos:]
    if remainder:
        parts.append(_format_text_segment(remainder))

    return "".join(parts)


def split_html_safe(text: str, max_len: int = 4000) -> list[str]:
    """
    Split a potentially large HTML string into chunks ≤ max_len chars.
    Tries to split on double newlines (paragraph boundaries).
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to find a paragraph break to split at
        cut = text.rfind("\n\n", 0, max_len)
        if cut == -1:
            cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    return chunks
