"""Find the HTTP calls a consumer service makes, by scanning its source code.

This version is pattern-based (regular expressions plus a small tokenizer), which
works across Python and JavaScript/TypeScript without a parser per language.
It recognizes:

    requests.get(...), httpx.post(...), self.session.get(...), client.delete(...)
    axios.get(...), api.post(...), fetch(url, { method: "PUT" })
    url = f"{BASE}/orders/{id}"; requests.get(url)          (URL in a variable)
    requests.get(self._url("orders", id))                   (URL from a helper)
    orders_api.get_order(id), ordersApi.getOrder(id)        (generated clients)

Comments, docstrings and strings are ignored, so `# requests.get("/x")` is not a call.
For each call it records the URL as a template ("/{}/orders/{}"), the HTTP method,
the code before and after the call in the same function, and, when the function
returns the response, the code after each call to that function in the same file.
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
HTTP_VERBS = {"get", "post", "put", "patch", "delete"}

# Generated-client calls are recorded with this URL prefix plus the method name, and
# matched to an operation by its operationId (see matcher.py).
OPERATION_PREFIX = "op:"
# Method names on client objects that are never API operations.
_NOT_OPERATIONS = {
    "request",
    "send",
    "close",
    "json",
    "text",
    "then",
    "catch",
    "finally",
    "head",
    "options",
    "stream",
    "build_request",
    "raise_for_status",
    "mount",
    "create",
}

_METHOD_CALL = re.compile(
    r"(?P<obj>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.(?P<method>[A-Za-z_$][\w$]*)\s*\("
)
_FETCH_CALL = re.compile(r"(?<![\w$.])fetch\s*\(")
_FETCH_METHOD = re.compile(r"""method\s*:\s*['"`](?P<method>[A-Za-z]+)['"`]""")

# A line that starts a function. Its name is in one of the `name` groups.
_FUNCTION = re.compile(
    r"^(?P<indent>[ \t]*)(?:"
    r"(?:async\s+)?def\s+(?P<py>\w+)"
    r"|(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<js>[\w$]+)"
    r"|(?:export\s+)?(?:const|let|var)\s+(?P<arrow>[\w$]+)\s*(?::[^=]+)?="
    r"\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*(?::[^=]+)?=>"
    r"|(?:(?:public|private|protected|static|async)\s+)*(?P<method>(?!(?:if|for|while|switch|catch|return)\b)[\w$]+)"
    r"\s*\([^)]*\)\s*(?::\s*[^{]+)?\{\s*$"
    r")"
)
_CLASS = re.compile(r"^[ \t]*(?:export\s+)?(?:default\s+)?class\s")
CONTEXT_LINES = 25
PREAMBLE_LINES = 25

_PLACEHOLDER = re.compile(r"\$?\{[^{}]*\}")  # f"{x}", `${x}`, "{}".format(...)
_SCHEME_HOST = re.compile(r"^[a-zA-Z][\w+.-]*://[^/]*")
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$.]*$")
_HELPER_CALL = re.compile(r"^(?P<name>[A-Za-z_$][\w$.]*)\s*\((?P<args>.*)\)$", re.DOTALL)


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
    python = file.endswith(".py")
    code_only = _mask(text, python, strings=True)  # where to look for calls
    no_comments = _mask(text, python, strings=False)  # what to read field usage from
    lines = text.splitlines()
    clean = no_comments.splitlines()
    found: list[tuple[int, str, str]] = []  # (offset, method, url)

    for m in _METHOD_CALL.finditer(code_only):
        obj, name = m.group("obj"), m.group("method")
        args = _call_args(text, m.end() - 1)
        if args is None:
            continue
        line = text.count("\n", 0, m.start())
        if name in HTTP_VERBS:
            if not any(hint in obj.rsplit(".", 1)[-1].lower() for hint in CLIENT_HINTS):
                continue
            url = _resolve_url(_first_arg(args), clean, line)
            if url:
                found.append((m.start(), name.upper(), url))
        elif _looks_like_generated_client(obj, name):
            found.append((m.start(), "*", OPERATION_PREFIX + name))

    for m in _FETCH_CALL.finditer(code_only):
        args = _call_args(text, m.end() - 1)
        if args is None:
            continue
        url = _resolve_url(_first_arg(args), clean, text.count("\n", 0, m.start()))
        if url:
            method = _FETCH_METHOD.search(args)
            found.append((m.start(), method.group("method").upper() if method else "GET", url))

    sites = []
    for offset, method, url in sorted(found):
        index = text.count("\n", 0, offset)  # 0-based line
        function, start = _enclosing_function(clean, index)
        context = _context(clean, index)
        sites.append(
            CallSite(
                consumer=consumer,
                file=file,
                line=index + 1,
                method=method,
                url=url,
                snippet=lines[index].strip(),
                context=context,
                preamble="\n".join(clean[max(start, index - PREAMBLE_LINES) : index]),
                function=function,
                callers=_callers(clean, function, start) if function else (),
            )
        )
    return sites


def _looks_like_generated_client(obj: str, name: str) -> bool:
    """`orders_api.get_order(...)`, `ordersApi.getOrder(...)`, `client.orders.list(...)`."""
    owner = obj.lower()
    return (
        any(hint in owner for hint in ("api", "client", "sdk", "service"))
        and name not in _NOT_OPERATIONS
        and not name.startswith("_")
        and len(name) > 2
    )


# --- where the URL comes from ------------------------------------------------


def _resolve_url(expr: str, lines: list[str], index: int) -> str | None:
    """A URL template for the first argument of a call, if it can be worked out."""
    expr = expr.strip()
    url = url_template(expr)
    if url:
        return url
    if _IDENTIFIER.match(expr):
        # `requests.get(url)`: look for the assignment above, in the function or module.
        assigned = re.compile(
            rf"^\s*(?:(?:const|let|var)\s+)?(?:self\.|this\.)?{re.escape(expr.split('.')[-1])}"
            r"\s*(?::[^=]+)?=(?!=)\s*(?P<value>.+?)\s*;?\s*$"
        )
        for i in range(index - 1, -1, -1):
            match = assigned.match(lines[i])
            if match:
                return url_template(match.group("value"))
        return None
    helper = _HELPER_CALL.match(expr)
    if helper and re.search(r"url|path|join|endpoint|route", helper.group("name"), re.I):
        # `self._url("orders", order_id)` or `urljoin(BASE, "orders/")`: treat the
        # arguments as path segments.
        segments = [
            url_template(a) or _literal(a) or "{}" for a in _split_args(helper.group("args"))
        ]
        return (
            url_template('"' + "/".join(s.strip("/") for s in segments) + '"') if segments else None
        )
    return None


def _literal(arg: str) -> str | None:
    arg = arg.strip()
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "'\"`":
        return arg[1:-1]
    return None


# --- comments, functions and context ---------------------------------------


def _mask(text: str, python: bool, strings: bool) -> str:
    """Blank out comments (and, if `strings`, string literals) keeping offsets and lines.

    Docstrings and other triple-quoted strings are always blanked: they're prose.
    """
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        if (python and c == "#") or (not python and text.startswith("//", i)):
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif not python and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif python and text.startswith(('"""', "'''"), i):
            end = text.find(text[i : i + 3], i + 3)
            end = n if end < 0 else end + 3
            blank(i, end)
            i = end
        elif c in "'\"`":
            end = _skip_string(text, i)
            if strings:
                blank(i, end)
            i = end
        else:
            i += 1
    return "".join(out)


def _enclosing_function(lines: list[str], index: int) -> tuple[str | None, int]:
    """(name, first line) of the function that line `index` is in; (None, 0) at top level."""
    indent = len(lines[index]) - len(lines[index].lstrip())
    for i in range(index, -1, -1):
        match = _FUNCTION.match(lines[i])
        if match and (i == index or len(match.group("indent")) < indent):
            if i == index:
                continue  # the call is on the function line itself (one-liner)
            name = next(g for g in ("py", "js", "arrow", "method") if match.group(g))
            return match.group(name), i
    return None, 0


def _context(lines: list[str], index: int) -> str:
    """The call's line plus following lines, up to the next function or class."""
    end = min(len(lines), index + CONTEXT_LINES)
    for i in range(index + 1, end):
        if _FUNCTION.match(lines[i]) or _CLASS.match(lines[i]):
            end = i
            break
    return "\n".join(lines[index:end])


def _callers(lines: list[str], function: str, start: int) -> tuple[tuple[int, str], ...]:
    """Code after each call to `function` elsewhere in the file: where its result is used."""
    call = re.compile(rf"(?<![\w$]){re.escape(function)}\s*\(")
    found = []
    for i, line in enumerate(lines):
        if i != start and call.search(line) and not _FUNCTION.match(line):
            found.append((i + 1, _context(lines, i)))
    return tuple(found)


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
    parts = _split_args(args or "")
    return parts[0] if parts else ""


def _split_args(args: str) -> list[str]:
    """Split call arguments on top-level commas."""
    parts, depth, start, i = [], 0, 0, 0
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
            parts.append(args[start:i])
            start = i + 1
        i += 1
    if args[start:].strip():
        parts.append(args[start:])
    return parts


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
