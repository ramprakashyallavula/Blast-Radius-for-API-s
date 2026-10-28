"""Loading OpenAPI documents and turning them into comparable operations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")

_PATH_PARAM = re.compile(r"\{[^}]+\}")


class SpecError(Exception):
    """Raised when a file is not a usable OpenAPI document."""


def normalize_path(path: str) -> str:
    """`/orders/{orderId}` and `/orders/{id}` are the same URL, so they're the same endpoint."""
    return _PATH_PARAM.sub("{}", path)


@dataclass(frozen=True)
class FieldInfo:
    """What we know about one field after flattening, e.g. `items[].sku`."""

    type: str | None  # "string", "integer", "string(date-time)", ... None if unspecified
    required: bool  # required within its parent object
    enum: tuple | None = None  # allowed values, if restricted


def _base_type(schema: dict) -> str | None:
    t = schema.get("type")
    if isinstance(t, list):
        # OpenAPI 3.1 writes nullable types as ["string", "null"].
        t = "|".join(sorted(x for x in t if x != "null")) or None
    if t is None:
        # Many specs leave out `type` when it's obvious from the other keys.
        if "properties" in schema:
            return "object"
        if "items" in schema:
            return "array"
    return t


def schema_type(schema: dict) -> str | None:
    """Type plus format, so `string` -> `string(date-time)` counts as a change."""
    t = _base_type(schema)
    if t and schema.get("format"):
        return f"{t}({schema['format']})"
    return t


@dataclass
class Operation:
    """One endpoint: an HTTP method on a path, with every `$ref` already resolved."""

    method: str  # upper case, e.g. "GET"
    path: str  # as written in the spec, e.g. "/orders/{orderId}"
    params: dict[tuple[str, str], dict] = field(default_factory=dict)  # (in, name) -> parameter
    request_body: dict | None = None
    responses: dict[str, dict] = field(default_factory=dict)  # status code -> response

    @property
    def key(self) -> tuple[str, str]:
        """Identity used to match the same endpoint across two spec versions."""
        return (self.method, normalize_path(self.path))

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


class Spec:
    """A parsed OpenAPI 3.x document."""

    def __init__(self, doc: Any, source: str = "<memory>"):
        if not isinstance(doc, dict):
            raise SpecError(f"{source}: expected a YAML/JSON object at the top level")

        version = str(doc.get("openapi", ""))
        if not version.startswith("3."):
            found = f"'{version}'" if version else "no 'openapi' field"
            raise SpecError(f"{source}: only OpenAPI 3.x is supported (found {found})")

        if not isinstance(doc.get("paths"), dict):
            raise SpecError(f"{source}: missing 'paths' section")

        self.doc = doc
        self.source = source

    @classmethod
    def load(cls, path: str | Path) -> Spec:
        """Read a spec from a .yaml, .yml or .json file."""
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise SpecError(f"could not read {path}: {e.strerror}") from e
        try:
            # YAML is a superset of JSON, so one parser handles both formats.
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise SpecError(f"{path} is not valid YAML/JSON: {e}") from e
        return cls(doc, str(path))

    @property
    def title(self) -> str:
        return str(self.doc.get("info", {}).get("title", "Untitled API"))

    @property
    def version(self) -> str:
        return str(self.doc.get("info", {}).get("version", "unknown"))

    @property
    def openapi_version(self) -> str:
        return str(self.doc["openapi"])

    @property
    def paths(self) -> list[str]:
        return list(self.doc["paths"])

    @property
    def base_path_segments(self) -> set[str]:
        """Path words from `servers`, e.g. {"v1"} for https://orders.example.com/v1.

        Consumers include these in their URLs, so the matcher allows them as a prefix.
        """
        segments: set[str] = set()
        for server in self.doc.get("servers") or []:
            url = server.get("url", "") if isinstance(server, dict) else ""
            for segment in urlparse(url).path.split("/"):
                if segment and "{" not in segment:  # skip server variables like {env}
                    segments.add(segment)
        return segments

    def resolve(self, node: Any) -> Any:
        """Follow `$ref` pointers until we reach a real definition.

        Only local refs (`#/components/...`) are supported for now. A ref can point
        to another ref, so we loop, and we remember what we've seen to catch cycles.
        """
        seen: set[str] = set()
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                raise SpecError(f"{self.source}: external $ref not supported yet: {ref}")
            if ref in seen:
                raise SpecError(f"{self.source}: circular $ref: {ref}")
            seen.add(ref)

            target: Any = self.doc
            for part in ref[2:].split("/"):
                # JSON Pointer escaping: "~1" means "/" and "~0" means "~".
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    raise SpecError(f"{self.source}: $ref points to nothing: {ref}")
                target = target[part]
            node = target
        return node

    def operations(self) -> dict[tuple[str, str], Operation]:
        """Every endpoint in the spec, keyed by (METHOD, normalized path)."""
        ops: dict[tuple[str, str], Operation] = {}
        for path, item in self.doc["paths"].items():
            item = self.resolve(item)
            if not isinstance(item, dict):
                continue
            # Parameters declared on the path apply to every method under it.
            shared = self._params(item.get("parameters"))

            for method in HTTP_METHODS:
                raw = item.get(method)
                if not isinstance(raw, dict):
                    continue
                op = Operation(
                    method=method.upper(),
                    path=path,
                    # A method-level parameter overrides a path-level one with the same name.
                    params={**shared, **self._params(raw.get("parameters"))},
                    request_body=self.resolve(raw["requestBody"]) if "requestBody" in raw else None,
                    # YAML reads an unquoted 200 as an int, so normalize codes to strings.
                    responses={
                        str(code): self.resolve(resp)
                        for code, resp in (raw.get("responses") or {}).items()
                    },
                )
                ops[op.key] = op
        return ops

    def _params(self, raw: Any) -> dict[tuple[str, str], dict]:
        out: dict[tuple[str, str], dict] = {}
        for param in raw or []:
            param = self.resolve(param)
            if isinstance(param, dict):
                out[(param.get("in", ""), param.get("name", ""))] = param
        return out

    # --- schemas ------------------------------------------------------------

    def json_schema(self, holder: dict | None) -> Any:
        """The JSON schema inside a requestBody or response object, if any."""
        content = (holder or {}).get("content") or {}
        for media_type, media in content.items():
            # Covers application/json and variants like application/problem+json.
            if "json" in media_type and isinstance(media, dict):
                return media.get("schema")
        return None

    def request_schema(self, op: Operation) -> Any:
        return self.json_schema(op.request_body)

    def success_schema(self, op: Operation) -> Any:
        """Schema of the first 2xx response with JSON content: what a happy-path client reads."""
        for code in sorted(op.responses):
            if code.startswith("2"):
                schema = self.json_schema(op.responses[code])
                if schema is not None:
                    return schema
        return None

    def flatten(
        self, schema: Any, prefix: str = "", required: bool = True, _stack: tuple = ()
    ) -> dict[str, FieldInfo]:
        """Turn a nested schema into {field path: FieldInfo}.

        Object properties are joined with ".", array items get "[]":
            {"items": [{"sku": ...}]}  ->  "items", "items[]", "items[].sku"
        """
        if schema is None:
            return {}
        expanded, stack = self._expand(schema, _stack)
        if expanded is None:
            # A schema that contains itself (e.g. a Category with child Categories).
            # Record the field but stop descending, or we'd recurse forever.
            return {prefix: FieldInfo("object", required)} if prefix else {}

        out: dict[str, FieldInfo] = {}
        if prefix:
            enum = expanded.get("enum")
            out[prefix] = FieldInfo(
                schema_type(expanded), required, tuple(enum) if isinstance(enum, list) else None
            )

        base = _base_type(expanded)
        if base == "array":
            out.update(self.flatten(expanded.get("items"), f"{prefix}[]", True, stack))
        elif base == "object":
            required_names = set(expanded.get("required") or [])
            for name, sub in (expanded.get("properties") or {}).items():
                child = f"{prefix}.{name}" if prefix else name
                out.update(self.flatten(sub, child, name in required_names, stack))
        return out

    def _expand(self, schema: Any, stack: tuple) -> tuple[dict | None, tuple]:
        """Resolve `$ref`, merge `allOf`, and simplify `anyOf`/`oneOf`.

        Returns (None, stack) on a recursive ref.
        """
        if not isinstance(schema, dict):
            return {}, stack
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in stack:
                return None, stack
            stack = (*stack, ref)
            schema = self.resolve(schema)
            if not isinstance(schema, dict):
                return {}, stack

        if "allOf" in schema:
            # allOf = "all of these at once", used for inheritance,
            # e.g. Order = BaseEntity + order fields.
            merged = {k: v for k, v in schema.items() if k != "allOf"}
            properties = dict(merged.get("properties") or {})
            required = list(merged.get("required") or [])
            for part in schema["allOf"]:
                sub, _ = self._expand(part, stack)
                if not sub:
                    continue
                properties.update(sub.get("properties") or {})
                required += sub.get("required") or []
                if not merged.get("type") and sub.get("type"):
                    merged["type"] = sub["type"]
            merged["properties"] = properties
            merged["required"] = required
            schema = merged

        for key in ("anyOf", "oneOf"):
            if key not in schema:
                continue
            rest = {k: v for k, v in schema.items() if k != key}
            variants = [
                v for v in schema[key] if not (isinstance(v, dict) and v.get("type") == "null")
            ]
            if len(variants) == 1:
                # "X or null" is how FastAPI/Pydantic write Optional[X]: treat it as X.
                sub, stack = self._expand(variants[0], stack)
                if sub is None:
                    return None, stack
                schema = {**rest, **sub}
            else:
                # A real union: compare it by the set of types it allows, e.g. "integer|string".
                types = set()
                for v in variants:
                    sub, _ = self._expand(v, stack)
                    types.add(schema_type(sub or {}) or "any")
                schema = {**rest, "type": "|".join(sorted(types))}
            break
        return schema, stack
