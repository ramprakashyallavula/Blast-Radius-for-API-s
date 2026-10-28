"""Compare two OpenAPI specs and classify every change.

The core rule: requests flow client -> server, so the server must keep accepting
everything it accepted before (it may loosen requests, never tighten them).
Responses flow server -> client, so the server may add to them but never take away.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from .models import Change
from .models import ChangeKind as K
from .models import Severity as S
from .spec import FieldInfo, Operation, Spec, schema_type

_TOKEN = re.compile(r"\[\]|[^.\[\]]+")


def field_ancestors(path: str) -> Iterator[str]:
    """`items[].sku` -> `items`, `items[]`, `items[].sku` (the path itself comes last)."""
    current = ""
    for token in _TOKEN.findall(path):
        if token == "[]":
            current += "[]"
        else:
            current = f"{current}.{token}" if current else token
        yield current


def leaf_name(path: str | None) -> str:
    """The name code actually uses: `items[].sku` -> `sku`."""
    names = [t for t in _TOKEN.findall(path or "") if t != "[]"]
    return names[-1] if names else (path or "")


def _topmost(paths: set[str]) -> list[str]:
    """Drop paths whose parent is also in the set: report `customer`, not every `customer.*`."""
    return [p for p in sorted(paths) if not any(a in paths for a in list(field_ancestors(p))[:-1])]


def _effectively_required(path: str, fields: dict[str, FieldInfo]) -> bool:
    """A required field inside an optional object only matters if the object is sent."""
    return all(fields[a].required for a in field_ancestors(path) if a in fields)


_PATH_NAMES = re.compile(r"\{([^}]+)\}")


def _param_schema(spec: Spec, param: dict) -> dict:
    schema, _ = spec._expand(param.get("schema") or {}, ())
    return schema or {}


def _enum_restriction(old_enum, new_enum) -> str | None:
    """How the allowed values got narrower, or None if they didn't."""
    if not isinstance(new_enum, (list, tuple)):
        return None  # unrestricted now: anything that worked before still does
    if not isinstance(old_enum, (list, tuple)):
        return f"now only accepts {_values(new_enum)}"
    removed = [v for v in old_enum if v not in new_enum]
    return f"no longer accepts {_values(removed)}" if removed else None


def _values(values) -> str:
    return ", ".join(f"`{v}`" for v in values)


def diff_specs(base: Spec, head: Spec) -> list[Change]:
    """Every change from `base` (what's live) to `head` (the proposed version)."""
    base_ops, head_ops = base.operations(), head.operations()
    changes: list[Change] = []

    for key, old in base_ops.items():
        new = head_ops.get(key)
        if new is None:
            changes.append(
                Change(K.ENDPOINT_REMOVED, S.BREAKING, old.method, old.path, "Endpoint was removed")
            )
        else:
            changes += _OperationDiff(base, head, old, new).run()

    for key, new in head_ops.items():
        if key not in base_ops:
            changes.append(Change(K.ENDPOINT_ADDED, S.SAFE, new.method, new.path, "New endpoint"))
    return changes


class _OperationDiff:
    """Compares one endpoint between the two versions."""

    def __init__(self, base: Spec, head: Spec, old: Operation, new: Operation):
        self.base, self.head, self.old, self.new = base, head, old, new
        self.changes: list[Change] = []

    def run(self) -> list[Change]:
        self._params()
        self._request()
        self._response()
        return self.changes

    def _add(self, kind, severity, message, field=None, before=None, after=None, certain=True):
        self.changes.append(
            Change(
                kind,
                severity,
                self.new.method,
                self.new.path,
                message,
                field,
                before,
                after,
                certain,
            )
        )

    # --- parameters --------------------------------------------------------

    def _params(self):
        self._path_params()
        old, new = self.old.params, self.new.params
        for key, param in new.items():
            location, name = key
            if location == "path":
                continue  # compared by position in _path_params
            required = bool(param.get("required"))
            before = old.get(key)
            if before is None:
                if required:
                    self._add(
                        K.PARAM_ADDED_REQUIRED,
                        S.BREAKING,
                        f"New required {location} parameter `{name}`",
                        name,
                    )
                else:
                    self._add(
                        K.PARAM_ADDED_OPTIONAL,
                        S.SAFE,
                        f"New optional {location} parameter `{name}`",
                        name,
                    )
                continue
            if required and not before.get("required"):
                self._add(
                    K.PARAM_BECAME_REQUIRED,
                    S.BREAKING,
                    f"{location.title()} parameter `{name}` is now required",
                    name,
                )
            old_schema = _param_schema(self.base, before)
            new_schema = _param_schema(self.head, param)
            old_type, new_type = schema_type(old_schema), schema_type(new_schema)
            if old_type != new_type:
                self._add(
                    K.PARAM_TYPE_CHANGED,
                    S.BREAKING,
                    f"{location.title()} parameter `{name}` changed type "
                    f"`{old_type}` → `{new_type}`",
                    name,
                    old_type,
                    new_type,
                )
            restriction = _enum_restriction(old_schema.get("enum"), new_schema.get("enum"))
            if restriction:
                self._add(
                    K.PARAM_ENUM_RESTRICTED,
                    S.BREAKING,
                    f"{location.title()} parameter `{name}` {restriction}",
                    name,
                )

        for location, name in old:
            if location != "path" and (location, name) not in new:
                self._add(
                    K.PARAM_REMOVED,
                    S.WARNING,
                    f"{location.title()} parameter `{name}` was removed",
                    name,
                )

    def _path_params(self):
        """Path parameters are matched by position, so renaming {id} to {orderId} is
        not a change, but the value they accept still has to stay compatible."""
        old_names = _PATH_NAMES.findall(self.old.path)
        new_names = _PATH_NAMES.findall(self.new.path)
        for old_name, new_name in zip(old_names, new_names, strict=True):
            before = self.old.params.get(("path", old_name))
            after = self.new.params.get(("path", new_name))
            if before is None or after is None:
                continue  # undeclared in one version: nothing to compare
            old_schema = _param_schema(self.base, before)
            new_schema = _param_schema(self.head, after)
            old_type, new_type = schema_type(old_schema), schema_type(new_schema)
            if old_type != new_type:
                self._add(
                    K.PATH_PARAM_TYPE_CHANGED,
                    S.BREAKING,
                    f"Path parameter `{new_name}` changed type `{old_type}` → `{new_type}`",
                    new_name,
                    old_type,
                    new_type,
                )
            restriction = _enum_restriction(old_schema.get("enum"), new_schema.get("enum"))
            if restriction:
                self._add(
                    K.PATH_PARAM_ENUM_RESTRICTED,
                    S.BREAKING,
                    f"Path parameter `{new_name}` {restriction}",
                    new_name,
                )

    # --- request body: may loosen, must not tighten ------------------------

    def _request(self):
        old_body, new_body = self.old.request_body or {}, self.new.request_body or {}
        if new_body.get("required") and not old_body.get("required"):
            self._add(K.REQUEST_BODY_BECAME_REQUIRED, S.BREAKING, "Request body is now required")

        if new_body:
            for media in sorted(Spec.media_types(old_body) - Spec.media_types(new_body)):
                self._add(
                    K.REQUEST_MEDIA_TYPE_REMOVED,
                    S.BREAKING,
                    f"Request body no longer accepts `{media}`",
                    before=media,
                )

        old_schema = self.base.request_schema(self.old)
        new_schema = self.head.request_schema(self.new)
        if old_schema is None or new_schema is None:
            return  # no JSON body before, or JSON is no longer accepted (reported above)

        old_variants = self.base.union_variants(old_schema)
        new_variants = self.head.union_variants(new_schema)
        if old_variants and (new_variants or 1) < old_variants:
            self._add(
                K.REQUEST_UNION_VARIANT_REMOVED,
                S.BREAKING,
                "Request body accepts fewer shapes "
                f"(oneOf/anyOf: {old_variants} → {new_variants or 1})",
                certain=False,
            )

        old = self.base.flatten(old_schema)
        new = self.head.flatten(new_schema)

        for path in _topmost(set(new) - set(old)):
            if _effectively_required(path, new):
                self._add(
                    K.REQUEST_FIELD_ADDED_REQUIRED,
                    S.BREAKING,
                    f"New required request field `{path}`",
                    path,
                )
            else:
                self._add(
                    K.REQUEST_FIELD_ADDED_OPTIONAL,
                    S.SAFE,
                    f"New optional request field `{path}`",
                    path,
                )

        for path in sorted(set(old) & set(new)):
            o, n = old[path], new[path]
            certain = not (o.in_union or n.in_union)
            if o.type != n.type:
                self._add(
                    K.REQUEST_FIELD_TYPE_CHANGED,
                    S.BREAKING,
                    f"Request field `{path}` changed type `{o.type}` → `{n.type}`",
                    path,
                    o.type,
                    n.type,
                    certain=certain,
                )
            if n.required and not o.required and _effectively_required(path, new):
                self._add(
                    K.REQUEST_FIELD_BECAME_REQUIRED,
                    S.BREAKING,
                    f"Request field `{path}` is now required",
                    path,
                    certain=certain,
                )
            restriction = _enum_restriction(o.enum, n.enum)
            if restriction:
                self._add(
                    K.REQUEST_ENUM_VALUE_REMOVED,
                    S.BREAKING,
                    f"Request field `{path}` {restriction}",
                    path,
                    certain=certain,
                )
            if o.nullable and not n.nullable:
                self._add(
                    K.REQUEST_FIELD_NO_LONGER_NULLABLE,
                    S.BREAKING,
                    f"Request field `{path}` no longer accepts null",
                    path,
                    certain=certain,
                )
            if o.variants and (n.variants or 1) < o.variants:
                self._add(
                    K.REQUEST_UNION_VARIANT_REMOVED,
                    S.BREAKING,
                    f"Request field `{path}` accepts fewer shapes "
                    f"(oneOf/anyOf: {o.variants} → {n.variants or 1})",
                    path,
                    certain=False,
                )

        for path in _topmost(set(old) - set(new)):
            self._add(
                K.REQUEST_FIELD_REMOVED,
                S.WARNING,
                f"Request field `{path}` was removed (clients still sending it may be rejected)",
                path,
                certain=not old[path].in_union,
            )

    # --- response: may add, must not take away -----------------------------

    def _response(self):
        old_codes = Spec.success_codes(self.old)
        new_codes = Spec.success_codes(self.new)
        for code in old_codes:
            if code not in self.new.responses:
                self._add(
                    K.RESPONSE_STATUS_REMOVED,
                    S.WARNING,
                    f"Success response `{code}` was removed",
                    before=code,
                )
        if old_codes:
            for code in new_codes:
                if code not in self.old.responses:
                    self._add(
                        K.RESPONSE_STATUS_ADDED,
                        S.WARNING,
                        f"Can now also respond `{code}`",
                        after=code,
                    )

        # Name the status code only when there's more than one success response.
        labelled = len(set(old_codes) | set(new_codes)) > 1
        for code in old_codes:
            if code in self.new.responses:
                self._response_body(code, f" `{code}`" if labelled else "")

    def _response_body(self, code: str, where: str):
        old_response, new_response = self.old.responses[code], self.new.responses[code]
        for media in sorted(Spec.media_types(old_response) - Spec.media_types(new_response)):
            self._add(
                K.RESPONSE_MEDIA_TYPE_REMOVED,
                S.BREAKING,
                f"Response{where} is no longer sent as `{media}`",
                before=media,
            )

        old_schema = self.base.json_schema(old_response)
        new_schema = self.head.json_schema(new_response)
        if old_schema is None or new_schema is None:
            return

        old_variants = self.base.union_variants(old_schema)
        new_variants = self.head.union_variants(new_schema)
        if (new_variants or 1) > (old_variants or 1):
            self._add(
                K.RESPONSE_UNION_VARIANT_ADDED,
                S.WARNING,
                f"Response{where} can now take more shapes "
                f"(oneOf/anyOf: {old_variants or 1} → {new_variants})",
                certain=False,
            )

        old = self.base.flatten(old_schema)
        new = self.head.flatten(new_schema)

        for path in _topmost(set(old) - set(new)):
            self._add(
                K.RESPONSE_FIELD_REMOVED,
                S.BREAKING,
                f"Response{where} field `{path}` was removed",
                path,
                certain=not old[path].in_union,
            )

        for path in sorted(set(old) & set(new)):
            o, n = old[path], new[path]
            certain = not (o.in_union or n.in_union)
            if o.type != n.type:
                self._add(
                    K.RESPONSE_FIELD_TYPE_CHANGED,
                    S.BREAKING,
                    f"Response{where} field `{path}` changed type `{o.type}` → `{n.type}`",
                    path,
                    o.type,
                    n.type,
                    certain=certain,
                )
            if o.required and not n.required:
                self._add(
                    K.RESPONSE_FIELD_BECAME_OPTIONAL,
                    S.WARNING,
                    f"Response{where} field `{path}` is no longer guaranteed (may be missing)",
                    path,
                    certain=certain,
                )
            if n.nullable and not o.nullable:
                self._add(
                    K.RESPONSE_FIELD_BECAME_NULLABLE,
                    S.WARNING,
                    f"Response{where} field `{path}` can now be null",
                    path,
                    certain=certain,
                )
            if o.enum and n.enum:
                added = [v for v in n.enum if v not in o.enum]
                if added:
                    self._add(
                        K.RESPONSE_ENUM_VALUE_ADDED,
                        S.WARNING,
                        f"Response{where} field `{path}` can now return {_values(added)}",
                        path,
                        after=added,
                        certain=certain,
                    )
            if (n.variants or 1) > (o.variants or 1):
                self._add(
                    K.RESPONSE_UNION_VARIANT_ADDED,
                    S.WARNING,
                    f"Response{where} field `{path}` can now take more shapes "
                    f"(oneOf/anyOf: {o.variants or 1} → {n.variants})",
                    path,
                    certain=False,
                )

        for path in _topmost(set(new) - set(old)):
            self._add(K.RESPONSE_FIELD_ADDED, S.SAFE, f"New response{where} field `{path}`", path)
