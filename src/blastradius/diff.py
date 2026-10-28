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

        # Compare each JSON media type with the same one in the new version. One that
        # disappeared is reported above; one that's new can't break existing clients.
        old_schemas = Spec.json_schemas(old_body)
        new_schemas = Spec.json_schemas(new_body)
        labelled = len(set(old_schemas) | set(new_schemas)) > 1
        for media, old_schema in old_schemas.items():
            if media in new_schemas:
                self._request_body(
                    old_schema, new_schemas[media], f" (`{media}`)" if labelled else ""
                )

    def _request_body(self, old_schema, new_schema, where: str):
        old_variants = self.base.union_variants(old_schema)
        new_variants = self.head.union_variants(new_schema)
        if old_variants and (new_variants or 1) < old_variants:
            self._add(
                K.REQUEST_UNION_VARIANT_REMOVED,
                S.BREAKING,
                f"Request body{where} accepts fewer shapes "
                f"(oneOf/anyOf: {old_variants} → {new_variants or 1})",
                certain=False,
            )

        old = self.base.flatten(old_schema)
        new = self.head.flatten(new_schema)
        self._request_fields(old, new, where)
        self._union_branches(old_schema, new_schema, new, where, request=True)

    def _request_fields(self, old, new, where, branch=None, context=None):
        """Request field rules. With `branch`, `old` and `new` are one union branch:
        everything found is uncertain, and safe changes aren't repeated."""
        note = f" (in {branch})" if branch else ""
        context = context or new  # decides whether a new required field's parents are sent

        for path in _topmost(set(new) - set(old)):
            if _effectively_required(path, context):
                self._add(
                    K.REQUEST_FIELD_ADDED_REQUIRED,
                    S.BREAKING,
                    f"New required request{where} field `{path}`{note}",
                    path,
                    certain=branch is None,
                )
            elif branch is None:
                self._add(
                    K.REQUEST_FIELD_ADDED_OPTIONAL,
                    S.SAFE,
                    f"New optional request{where} field `{path}`",
                    path,
                )

        for path in sorted(set(old) & set(new)):
            o, n = old[path], new[path]
            certain = branch is None and not (o.in_union or n.in_union)
            if o.type != n.type:
                self._add(
                    K.REQUEST_FIELD_TYPE_CHANGED,
                    S.BREAKING,
                    f"Request{where} field `{path}` changed type `{o.type}` → `{n.type}`{note}",
                    path,
                    o.type,
                    n.type,
                    certain=certain,
                )
            if n.required and not o.required and _effectively_required(path, context):
                self._add(
                    K.REQUEST_FIELD_BECAME_REQUIRED,
                    S.BREAKING,
                    f"Request{where} field `{path}` is now required{note}",
                    path,
                    certain=certain,
                )
            restriction = _enum_restriction(o.enum, n.enum)
            if restriction:
                self._add(
                    K.REQUEST_ENUM_VALUE_REMOVED,
                    S.BREAKING,
                    f"Request{where} field `{path}` {restriction}{note}",
                    path,
                    certain=certain,
                )
            if o.nullable and not n.nullable:
                self._add(
                    K.REQUEST_FIELD_NO_LONGER_NULLABLE,
                    S.BREAKING,
                    f"Request{where} field `{path}` no longer accepts null{note}",
                    path,
                    certain=certain,
                )
            if o.variants and (n.variants or 1) < o.variants:
                self._add(
                    K.REQUEST_UNION_VARIANT_REMOVED,
                    S.BREAKING,
                    f"Request{where} field `{path}` accepts fewer shapes "
                    f"(oneOf/anyOf: {o.variants} → {n.variants or 1}){note}",
                    path,
                    certain=False,
                )

        for path in _topmost(set(old) - set(new)):
            self._add(
                K.REQUEST_FIELD_REMOVED,
                S.WARNING,
                f"Request{where} field `{path}` was removed{note} "
                "(clients still sending it may be rejected)",
                path,
                certain=branch is None and not old[path].in_union,
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

        # Each JSON media type against the same one in the new version, whatever the order.
        old_schemas = Spec.json_schemas(old_response)
        new_schemas = Spec.json_schemas(new_response)
        labelled = len(set(old_schemas) | set(new_schemas)) > 1
        for media, old_schema in old_schemas.items():
            if media in new_schemas:
                label = f"{where} (`{media}`)" if labelled else where
                self._response_schema(old_schema, new_schemas[media], label)

    def _response_schema(self, old_schema, new_schema, where: str):
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
        self._response_fields(old, new, where)
        self._union_branches(old_schema, new_schema, new, where, request=False)

    def _response_fields(self, old, new, where, branch=None, context=None):
        """Response field rules; see _request_fields for what `branch` changes."""
        note = f" (in {branch})" if branch else ""

        for path in _topmost(set(old) - set(new)):
            self._add(
                K.RESPONSE_FIELD_REMOVED,
                S.BREAKING,
                f"Response{where} field `{path}` was removed{note}",
                path,
                certain=branch is None and not old[path].in_union,
            )

        for path in sorted(set(old) & set(new)):
            o, n = old[path], new[path]
            certain = branch is None and not (o.in_union or n.in_union)
            if o.type != n.type:
                self._add(
                    K.RESPONSE_FIELD_TYPE_CHANGED,
                    S.BREAKING,
                    f"Response{where} field `{path}` changed type `{o.type}` → `{n.type}`{note}",
                    path,
                    o.type,
                    n.type,
                    certain=certain,
                )
            if o.required and not n.required:
                self._add(
                    K.RESPONSE_FIELD_BECAME_OPTIONAL,
                    S.WARNING,
                    f"Response{where} field `{path}` is no longer guaranteed "
                    f"(may be missing){note}",
                    path,
                    certain=certain,
                )
            if n.nullable and not o.nullable:
                self._add(
                    K.RESPONSE_FIELD_BECAME_NULLABLE,
                    S.WARNING,
                    f"Response{where} field `{path}` can now be null{note}",
                    path,
                    certain=certain,
                )
            if o.enum and n.enum:
                added = [v for v in n.enum if v not in o.enum]
                if added:
                    self._add(
                        K.RESPONSE_ENUM_VALUE_ADDED,
                        S.WARNING,
                        f"Response{where} field `{path}` can now return {_values(added)}{note}",
                        path,
                        after=added,
                        certain=certain,
                    )
            if (n.variants or 1) > (o.variants or 1):
                self._add(
                    K.RESPONSE_UNION_VARIANT_ADDED,
                    S.WARNING,
                    f"Response{where} field `{path}` can now take more shapes "
                    f"(oneOf/anyOf: {o.variants or 1} → {n.variants}){note}",
                    path,
                    certain=False,
                )

        if branch is None:
            for path in _topmost(set(new) - set(old)):
                self._add(
                    K.RESPONSE_FIELD_ADDED, S.SAFE, f"New response{where} field `{path}`", path
                )

    # --- unions, branch by branch ------------------------------------------

    def _union_branches(self, old_schema, new_schema, new_fields, where, request: bool):
        """Compare paired oneOf/anyOf branches one to one.

        The merged (superset) view hides a change in one branch when another branch
        still has the field as before. Branches are paired by `$ref` name or `title`
        when every branch has one, else by position when the counts match; otherwise
        only the merged view applies. Everything found here is uncertain (which branch
        a payload uses can't be known statically) and skipped if already reported.
        """
        compare = self._request_fields if request else self._response_fields
        old_unions, new_unions = self.base.unions(old_schema), self.head.unions(new_schema)
        reported = {(c.kind, c.field) for c in self.changes}

        for prefix in sorted(set(old_unions) & set(new_unions)):
            pairs, removed, added = _pair_branches(old_unions[prefix], new_unions[prefix])
            for label, old_branch, new_branch in pairs:
                old = self.base.flatten(old_branch, prefix, _in_union=True)
                new = self.head.flatten(new_branch, prefix, _in_union=True)
                # Whether the union itself is sent depends on its parents, not the branch.
                context = {**new, **{a: new_fields[a] for a in _ancestors(prefix, new_fields)}}
                start = len(self.changes)
                compare(old, new, where, branch=label, context=context)
                found, self.changes[start:] = self.changes[start:], []
                for change in found:
                    if (change.kind, change.field) not in reported:
                        reported.add((change.kind, change.field))
                        self.changes.append(change)

            at = f" field `{prefix}`" if prefix else ""
            kind = K.REQUEST_UNION_VARIANT_REMOVED if request else K.RESPONSE_UNION_VARIANT_ADDED
            if (kind, prefix or None) in reported:
                continue  # the branch count already shows it
            if request:
                for name in removed:
                    self._add(
                        kind,
                        S.BREAKING,
                        f"Request{where}{at} no longer accepts the `{name}` shape",
                        prefix or None,
                        certain=False,
                    )
            else:
                for name in added:
                    self._add(
                        kind,
                        S.WARNING,
                        f"Response{where}{at} can now be the `{name}` shape",
                        prefix or None,
                        certain=False,
                    )


def _ancestors(prefix: str, fields: dict[str, FieldInfo]) -> list[str]:
    return [a for a in field_ancestors(prefix) if a in fields] if prefix else []


def _branch_name(branch) -> str | None:
    if isinstance(branch, dict):
        if isinstance(branch.get("$ref"), str):
            return branch["$ref"].rsplit("/", 1)[-1]
        if isinstance(branch.get("title"), str):
            return branch["title"]
    return None


def _pair_branches(old: list, new: list) -> tuple[list, list[str], list[str]]:
    """(label, old branch, new branch) pairs, plus names of removed and added branches."""
    old_names = [_branch_name(b) for b in old]
    new_names = [_branch_name(b) for b in new]
    named = all(old_names) and all(new_names)
    if named and len(set(old_names)) == len(old) and len(set(new_names)) == len(new):
        pairs = [
            (f"the `{name}` branch", branch, new[new_names.index(name)])
            for name, branch in zip(old_names, old, strict=True)
            if name in new_names
        ]
        removed = [n for n in old_names if n not in new_names]
        added = [n for n in new_names if n not in old_names]
        return pairs, removed, added
    if len(old) == len(new):
        return (
            [
                (f"branch {i} of the oneOf/anyOf", o, n)
                for i, (o, n) in enumerate(zip(old, new, strict=True), start=1)
            ],
            [],
            [],
        )
    return [], [], []
