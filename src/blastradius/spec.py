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


# Marks a schema that _expand built by merging the branches of a real anyOf/oneOf.
# Its value is the number of non-null branches.
UNION_VARIANTS = "x-blastradius-union-variants"


@dataclass(frozen=True)
class FieldInfo:
    """What we know about one field after flattening, e.g. `items[].sku`."""

    type: str | None  # "string", "integer", "string(date-time)", ... None if unspecified
    required: bool  # required within its parent object
    enum: tuple | None = None  # allowed values, if restricted
    nullable: bool = False  # may be null (nullable: true, type: [X, "null"], anyOf [X, null])
    variants: int | None = None  # for a real anyOf/oneOf: how many non-null branches
    # True if the field sits inside a real anyOf/oneOf branch: whether a given
    # response or request uses that branch can't be known statically.
    in_union: bool = False


def _is_null_schema(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("type") == "null"


def _is_nullable(schema: dict) -> bool:
    t = schema.get("type")
    return schema.get("nullable") is True or (isinstance(t, list) and "null" in t)


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
    operation_id: str | None = None  # used to match generated-client calls like getOrder()

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
        return cls.from_text(text, str(path))

    @classmethod
    def from_text(cls, text: str, source: str = "<memory>") -> Spec:
        """Parse a spec from YAML or JSON text."""
        try:
            # YAML is a superset of JSON, so one parser handles both formats.
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise SpecError(f"{source} is not valid YAML/JSON: {e}") from e
        return cls(doc, source)

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
                raise SpecError(
                    f"{self.source}: external $ref not supported yet: {ref}. Bundle the "
                    "spec into one file first, e.g. "
                    "`npx @redocly/cli bundle openapi.yaml -o bundled.yaml`"
                )
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
                    operation_id=raw.get("operationId"),
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
        """The schema of the first JSON media type in a requestBody or response, if any."""
        return next(iter(self.json_schemas(holder).values()), None)

    @staticmethod
    def json_schemas(holder: dict | None) -> dict[str, Any]:
        """Every JSON media type in a requestBody or response, with its schema.

        Covers application/json and variants like application/problem+json. Keyed by
        media type, so two versions are compared type by type whatever their order.
        """
        content = (holder or {}).get("content") or {}
        return {
            media_type: media.get("schema")
            for media_type, media in content.items()
            if "json" in media_type and isinstance(media, dict) and media.get("schema") is not None
        }

    def request_schema(self, op: Operation) -> Any:
        return self.json_schema(op.request_body)

    @staticmethod
    def media_types(holder: dict | None) -> set[str]:
        """Content types a requestBody or response offers, e.g. {"application/json"}."""
        return set((holder or {}).get("content") or {})

    @staticmethod
    def success_codes(op: Operation) -> list[str]:
        return sorted(code for code in op.responses if code.startswith("2"))

    def success_schema(self, op: Operation) -> Any:
        """Schema of the first 2xx response with JSON content: what a happy-path client reads."""
        for code in sorted(op.responses):
            if code.startswith("2"):
                schema = self.json_schema(op.responses[code])
                if schema is not None:
                    return schema
        return None

    def flatten(
        self,
        schema: Any,
        prefix: str = "",
        required: bool = True,
        _stack: tuple = (),
        _in_union: bool = False,
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
            info = FieldInfo("object", required, in_union=_in_union)
            return {prefix: info} if prefix else {}

        variants = expanded.get(UNION_VARIANTS)
        out: dict[str, FieldInfo] = {}
        if prefix:
            enum = expanded.get("enum")
            out[prefix] = FieldInfo(
                schema_type(expanded),
                required,
                tuple(enum) if isinstance(enum, list) else None,
                nullable=_is_nullable(expanded),
                variants=variants,
                in_union=_in_union,
            )

        # Everything below a real union belongs to some branch, not necessarily all.
        children_in_union = _in_union or variants is not None
        kinds = set((_base_type(expanded) or "").split("|"))
        if "array" in kinds and expanded.get("items") is not None:
            out.update(
                self.flatten(expanded["items"], f"{prefix}[]", True, stack, children_in_union)
            )
        if "object" in kinds or "properties" in expanded:
            required_names = set(expanded.get("required") or [])
            for name, sub in (expanded.get("properties") or {}).items():
                child = f"{prefix}.{name}" if prefix else name
                out.update(
                    self.flatten(sub, child, name in required_names, stack, children_in_union)
                )
        return out

    def unions(self, schema: Any, prefix: str = "", _stack: tuple = ()) -> dict[str, list]:
        """Every real anyOf/oneOf below `schema`: {field path: its non-null branches}.

        Paths match `flatten`'s ("" for the schema itself), so a branch can be
        flattened under the same prefix and compared with its counterpart.
        """
        out: dict[str, list] = {}
        if not isinstance(schema, dict):
            return out
        if "$ref" in schema:
            if schema["$ref"] in _stack:
                return out
            _stack = (*_stack, schema["$ref"])
            schema = self.resolve(schema)
            if not isinstance(schema, dict):
                return out

        def collect(sub: Any, path: str) -> None:
            for key, value in self.unions(sub, path, _stack).items():
                out.setdefault(key, value)

        for part in schema.get("allOf") or []:
            collect(part, prefix)
        for key in ("anyOf", "oneOf"):
            if isinstance(schema.get(key), list):
                variants = [v for v in schema[key] if not _is_null_schema(v)]
                if len(variants) > 1:
                    out.setdefault(prefix, variants)
                for variant in variants:
                    collect(variant, prefix)
        for name, sub in (schema.get("properties") or {}).items():
            collect(sub, f"{prefix}.{name}" if prefix else name)
        if schema.get("items") is not None:
            collect(schema["items"], f"{prefix}[]")
        return out

    def union_variants(self, schema: Any) -> int | None:
        """Branch count if the schema is a real anyOf/oneOf, else None."""
        expanded, _ = self._expand(schema, ())
        return (expanded or {}).get(UNION_VARIANTS)

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
            if not isinstance(schema.get(key), list):
                continue
            rest = {k: v for k, v in schema.items() if k != key}
            branches = schema[key]
            variants = [v for v in branches if not _is_null_schema(v)]
            if len(variants) < len(branches):
                rest["nullable"] = True
            if len(variants) == 1:
                # "X or null" is how FastAPI/Pydantic write Optional[X]: treat it as a
                # nullable X, so changes inside X are still compared exactly.
                sub, stack = self._expand(variants[0], stack)
                if sub is None:
                    return None, stack
                schema = {**rest, **sub}
                if rest.get("nullable"):
                    schema["nullable"] = True
            elif variants:
                schema = self._merge_union(rest, variants, stack)
            break
        return schema, stack

    def _merge_union(self, rest: dict, variants: list, stack: tuple) -> dict:
        """A superset view of a real union, so changes inside its branches stay visible.

        The type lists every branch's type ("integer|string"). Properties from all
        branches are merged; a property only some branches have becomes optional, and
        one that branches define differently becomes a union of those definitions.
        Fields below are flagged `in_union`, because which branch a payload uses
        can't be known statically, so impact on consumers is uncertain.
        """
        expanded = []
        for v in variants:
            sub, _ = self._expand(v, stack)
            expanded.append(sub if sub is not None else {"type": "object"})

        merged = {
            **rest,
            "type": "|".join(sorted({schema_type(s) or "any" for s in expanded})),
            UNION_VARIANTS: len(variants),
        }
        properties: dict[str, list] = {}
        objects = [s for s in expanded if "properties" in s or _base_type(s) == "object"]
        for s in objects:
            for name, sub in (s.get("properties") or {}).items():
                properties.setdefault(name, []).append(sub)
        if properties:
            merged["properties"] = {name: _one_or_union(subs) for name, subs in properties.items()}
            # Required only if every branch is an object that requires it.
            if len(objects) == len(expanded):
                required = [set(s.get("required") or []) for s in objects]
                merged["required"] = sorted(set.intersection(*required))
            else:
                merged["required"] = []
        items = [s["items"] for s in expanded if s.get("items") is not None]
        if items:
            merged["items"] = _one_or_union(items)
        return merged


def _one_or_union(schemas: list) -> Any:
    unique = []
    for s in schemas:
        if s not in unique:
            unique.append(s)
    return unique[0] if len(unique) == 1 else {"anyOf": unique}
