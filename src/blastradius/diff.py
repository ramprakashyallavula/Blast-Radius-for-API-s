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


def _param_type(spec: Spec, param: dict) -> str | None:
    schema, _ = spec._expand(param.get("schema") or {}, ())
    return schema_type(schema or {})


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

    def _add(self, kind, severity, message, field=None, before=None, after=None):
        self.changes.append(
            Change(kind, severity, self.new.method, self.new.path, message, field, before, after)
        )

    # --- parameters (query, header, cookie) --------------------------------

    def _params(self):
        old, new = self.old.params, self.new.params
        for key, param in new.items():
            location, name = key
            if location == "path":
                continue  # path parameters are part of the URL, already compared via the key
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
            old_type = _param_type(self.base, before)
            new_type = _param_type(self.head, param)
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

        for location, name in old:
            if location != "path" and (location, name) not in new:
                self._add(
                    K.PARAM_REMOVED,
                    S.WARNING,
                    f"{location.title()} parameter `{name}` was removed",
                    name,
                )

    # --- request body: may loosen, must not tighten ------------------------

    def _request(self):
        old_body, new_body = self.old.request_body or {}, self.new.request_body or {}
        if new_body.get("required") and not old_body.get("required"):
            self._add(K.REQUEST_BODY_BECAME_REQUIRED, S.BREAKING, "Request body is now required")

        old = self.base.flatten(self.base.request_schema(self.old))
        new = self.head.flatten(self.head.request_schema(self.new))

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
            if o.type != n.type:
                self._add(
                    K.REQUEST_FIELD_TYPE_CHANGED,
                    S.BREAKING,
                    f"Request field `{path}` changed type `{o.type}` → `{n.type}`",
                    path,
                    o.type,
                    n.type,
                )
            if n.required and not o.required and _effectively_required(path, new):
                self._add(
                    K.REQUEST_FIELD_BECAME_REQUIRED,
                    S.BREAKING,
                    f"Request field `{path}` is now required",
                    path,
                )
            if o.enum and n.enum:
                removed = [v for v in o.enum if v not in n.enum]
                if removed:
                    self._add(
                        K.REQUEST_ENUM_VALUE_REMOVED,
                        S.BREAKING,
                        f"Request field `{path}` no longer accepts {_values(removed)}",
                        path,
                        removed,
                    )

        for path in _topmost(set(old) - set(new)):
            self._add(
                K.REQUEST_FIELD_REMOVED,
                S.WARNING,
                f"Request field `{path}` was removed (clients still sending it may be rejected)",
                path,
            )

    # --- response: may add, must not take away -----------------------------

    def _response(self):
        for code in sorted(self.old.responses):
            if code.startswith("2") and code not in self.new.responses:
                self._add(
                    K.RESPONSE_STATUS_REMOVED,
                    S.WARNING,
                    f"Success response `{code}` was removed",
                    before=code,
                )

        old = self.base.flatten(self.base.success_schema(self.old))
        new = self.head.flatten(self.head.success_schema(self.new))

        for path in _topmost(set(old) - set(new)):
            self._add(
                K.RESPONSE_FIELD_REMOVED, S.BREAKING, f"Response field `{path}` was removed", path
            )

        for path in sorted(set(old) & set(new)):
            o, n = old[path], new[path]
            if o.type != n.type:
                self._add(
                    K.RESPONSE_FIELD_TYPE_CHANGED,
                    S.BREAKING,
                    f"Response field `{path}` changed type `{o.type}` → `{n.type}`",
                    path,
                    o.type,
                    n.type,
                )
            if o.required and not n.required:
                self._add(
                    K.RESPONSE_FIELD_BECAME_OPTIONAL,
                    S.WARNING,
                    f"Response field `{path}` is no longer guaranteed (may be missing)",
                    path,
                )
            if o.enum and n.enum:
                added = [v for v in n.enum if v not in o.enum]
                if added:
                    self._add(
                        K.RESPONSE_ENUM_VALUE_ADDED,
                        S.WARNING,
                        f"Response field `{path}` can now return {_values(added)}",
                        path,
                        after=added,
                    )

        for path in _topmost(set(new) - set(old)):
            self._add(K.RESPONSE_FIELD_ADDED, S.SAFE, f"New response field `{path}`", path)
