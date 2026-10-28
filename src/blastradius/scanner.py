"""Find the HTTP calls a consumer service makes, by scanning its source code.

This version is pattern-based (regular expressions plus a small string tokenizer),
which works across Python and JavaScript/TypeScript without a parser per language.
It recognizes:

    requests.get(...), httpx.post(...), self.session.get(...), client.delete(...)
    axios.get(...), api.post(...), fetch(url, { method: "PUT" })

For each call it records the URL as a template ("/{}/orders/{}"), the HTTP method,
and the code that follows the call, which later steps search for field usage.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from .models import CallSite

SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "coverage",
    ".pytest_cache",
}

# Only treat `<object>.get(...)` as an HTTP call if the object's name looks like an
# HTTP client. Otherwise every `my_dict.get("key")` would count.
CLIENT_HINTS = ("requests", "httpx", "session", "client", "http", "axios", "api")

_METHOD_CALL = re.compile(
    r"(?P<obj>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.(?P<method>get|post|put|patch|delete)\s*\("
)
_FETCH_CALL = re.compile(r"(?<![\w$.])fetch\s*\(")
_FETCH_METHOD = re.compile(r"""method\s*:\s*['"`](?P<method>[A-Za-z]+)['"`]""")

# Where one function ends and the next begins. The context of a call stops here.
_BOUNDARY = re.compile(
    r"^\s*(?:async\s+)?def\s|^\s*class\s|^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b"
)
CONTEXT_LINES = 25

_PLACEHOLDER = re.compile(r"\$?\{[^{}]*\}")  # f"{x}", `${x}`, "{}".format(...)
_SCHEME_HOST = re.compile(r"^[a-zA-Z][\w+.-]*://[^/]*")


def scan_consumer(root: str | Path, name: str | None = None) -> list[CallSite]:
    """All HTTP call sites in one consumer's source tree."""
    root = Path(root)
    name = name or root.name
    sites: list[CallSite] = []
    for file in _source_files(root):
        text = file.read_text(encoding="utf-8", errors="replace")
        sites += scan_text(text, consumer=name, file=file.relative_to(root).as_posix())
    return sites


def _source_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if (
            path.suffix in SOURCE_SUFFIXES
            and path.is_file()
            and not SKIP_DIRS.intersection(path.relative_to(root).parts)
        ):
            yield path


def scan_text(text: str, consumer: str, file: str) -> list[CallSite]:
    """All HTTP call sites in one source file."""
    lines = text.splitlines()
    found: list[tuple[int, str, str]] = []  # (offset, method, url)

    for m in _METHOD_CALL.finditer(text):
        client = m.group("obj").rsplit(".", 1)[-1].lower()
        if not any(hint in client for hint in CLIENT_HINTS):
            continue
        args = _call_args(text, m.end() - 1)
        url = url_template(_first_arg(args)) if args is not None else None
        if url:
            found.append((m.start(), m.group("method").upper(), url))

    for m in _FETCH_CALL.finditer(text):
        args = _call_args(text, m.end() - 1)
        url = url_template(_first_arg(args)) if args is not None else None
        if url:
            method = _FETCH_METHOD.search(args)
            found.append((m.start(), method.group("method").upper() if method else "GET", url))

    sites = []
    for offset, method, url in sorted(found):
        line = text.count("\n", 0, offset) + 1
        sites.append(
            CallSite(
                consumer=consumer,
                file=file,
                line=line,
                method=method,
                url=url,
                snippet=lines[line - 1].strip(),
                context=_context(lines, line),
            )
        )
    return sites


def _context(lines: list[str], line: int) -> str:
    """The call's line plus following lines, up to the next function or class."""
    end = min(len(lines), line - 1 + CONTEXT_LINES)
    for i in range(line, end):
        if _BOUNDARY.match(lines[i]):
            end = i
            break
    return "\n".join(lines[line - 1 : end])


# --- reading arguments out of source text ----------------------------------


def _call_args(text: str, open_paren: int) -> str | None:
    """Text between `(` at `open_paren` and its matching `)`, skipping over strings."""
    depth, i, n = 0, open_paren, min(len(text), open_paren + 5000)
    while i < n:
        c = text[i]
        if c in "'\"`":
            i = _skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1 : i]
        i += 1
    return None


def _first_arg(args: str | None) -> str:
    """Everything before the first top-level comma."""
    if not args:
        return ""
    depth, i = 0, 0
    while i < len(args):
        c = args[i]
        if c in "'\"`":
            i = _skip_string(args, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            return args[:i]
        i += 1
    return args


def _skip_string(text: str, start: int) -> int:
    """Index just past the string literal that starts at `start`."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        if quote != "`" and text[i] == "\n":
            return i  # unterminated single-line string: give up at end of line
        i += 1
    return i


def url_template(expr: str) -> str | None:
    """Turn the URL expression in a call into a template, or None if it isn't a URL.

        f"{BASE}/orders/{order_id}"      -> "/{}/orders/{}"
        BASE + "/orders/" + str(id)      -> "/{}/orders/{}"
        `${API}/orders/${id}?full=1`     -> "/{}/orders/{}"
        "https://x.com/v1/orders"        -> "/v1/orders"
        url                              -> None (nothing literal to go on)

    String literals are kept; everything else (variables, function calls) becomes `{}`.
    """
    parts: list[str] = []
    saw_literal = False
    dynamic = False  # code outside a string literal since the last literal
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c in "'\"`":
            end = _skip_string(expr, i)
            if dynamic:
                parts.append("{}")
                dynamic = False
            parts.append(_PLACEHOLDER.sub("{}", expr[i + 1 : end - 1]))
            saw_literal = True
            i = end
            continue
        is_string_prefix = c in "fFrRbBuU" and i + 1 < n and expr[i + 1] in "'\""
        if not (c.isspace() or c == "+" or is_string_prefix):
            dynamic = True
        i += 1
    if dynamic:
        parts.append("{}")
    if not saw_literal:
        return None

    url = re.sub(r"(\{\})+", "{}", "".join(parts))  # "{}{}" -> "{}"
    url = url.split("?", 1)[0].split("#", 1)[0]
    url = _SCHEME_HOST.sub("", url)
    segments = [s for s in url.split("/") if s]
    # Needs at least one real path word; "/{}" alone could be anything.
    if "/" not in url or not any("{}" not in s for s in segments):
        return None
    return "/" + "/".join(segments)
