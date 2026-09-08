"""Pure helpers for Arrangements — no IC-runtime dependencies, so they can be
unit-tested without a replica or the Basilisk CDK installed.

An arrangement's post-deploy `steps` are declarative calls, each a JSON object:

    {"target": "<canister name or id>", "method": "<method>", "args": <json|null>}

`args` is passed to the target method as a single Candid `text` argument (the
JSON-text API style every Casals-managed canister uses): a JSON value is encoded
as its JSON-string form, a bare string is passed through verbatim, and
`null`/absent means a no-argument call. Validation happens at write time so a
malformed arrangement is rejected when it is set, not silently at apply time.
"""

import json

_SCHEMA_TYPES = frozenset({"text", "principal", "bool", "number", "sha256"})


def _match_param_ref(value):
    if not isinstance(value, str) or not value.startswith("$"):
        return None
    name = value[1:]
    if not name:
        return None
    first = name[0]
    if not (first.isalpha() or first == "_"):
        return None
    for ch in name[1:]:
        if not (ch.isalnum() or ch == "_"):
            return None
    return name


def candid_text_tuple(s: str) -> str:
    """Wrap a string as a Candid text-literal tuple `("...")`, escaping backslashes
    and quotes. Mirrors the encoding the seed/test harness uses for text-in calls."""
    escaped = (s or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def step_text_arg(args):
    """Render a step's `args` as the text argument to pass, or None for a no-arg
    call. A string is passed verbatim; any other JSON value is serialized."""
    if args is None:
        return None
    if isinstance(args, str):
        return args
    return json.dumps(args)


def validate_and_normalize_steps(steps):
    """Validate and normalize an arrangement's `steps`.

    Accepts a list (or a JSON string encoding one) and returns a clean list of
    `{"target", "method", "args"}` dicts. Raises ValueError on malformed input.
    """
    if isinstance(steps, str):
        try:
            steps = json.loads(steps or "[]")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"steps is not valid JSON: {e}")
    if steps is None:
        return []
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    out = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"step {i} must be an object")
        target = (step.get("target") or "").strip()
        method = (step.get("method") or "").strip()
        if not target:
            raise ValueError(f"step {i} missing 'target'")
        if not method:
            raise ValueError(f"step {i} missing 'method'")
        out.append({"target": target, "method": method, "args": step.get("args", None)})
    return out


def normalize_execute_principals(value):
    """Validate and normalize execute_principals to a unique list of principals.

    Accepts a list or a JSON string encoding one. Raises ValueError on malformed
    input.
    """
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            value = json.loads(s)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"execute_principals is not valid JSON: {e}")
    if not isinstance(value, list):
        raise ValueError("execute_principals must be a JSON array")
    out = []
    seen = set()
    for item in value:
        principal = str(item or "").strip()
        if not principal or principal in seen:
            continue
        seen.add(principal)
        out.append(principal)
    return out


def parse_execute_principals_json(stored: str) -> list:
    """Parse stored execute_principals_json; return [] on empty or malformed."""
    raw = (stored or "").strip()
    if not raw:
        return []
    try:
        return normalize_execute_principals(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return []


def normalize_parameters(parameters):
    """Validate that `parameters` is a JSON object; return it as a dict.

    Accepts a dict or a JSON string encoding one. Raises ValueError otherwise."""
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters or "{}")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"parameters is not valid JSON: {e}")
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a JSON object")
    return parameters


def normalize_parameter_schema(schema):
    """Validate parameter_schema: { name: { type, label?, description?, required? } }."""
    if isinstance(schema, str):
        try:
            schema = json.loads(schema or "{}")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"parameter_schema is not valid JSON: {e}")
    if schema is None:
        return {}
    if not isinstance(schema, dict):
        raise ValueError("parameter_schema must be a JSON object")
    out = {}
    for key, spec in schema.items():
        name = str(key or "").strip()
        if not name:
            raise ValueError("parameter_schema keys must be non-empty strings")
        if not isinstance(spec, dict):
            raise ValueError(f"parameter_schema['{name}'] must be an object")
        ptype = (spec.get("type") or "text").strip().lower()
        if ptype not in _SCHEMA_TYPES:
            raise ValueError(f"parameter_schema['{name}'].type must be one of: {sorted(_SCHEMA_TYPES)}")
        out[name] = {
            "type": ptype,
            "label": str(spec.get("label") or name)[:128],
            "description": str(spec.get("description") or "")[:512],
            "required": bool(spec.get("required")),
        }
    return out


def parse_parameter_schema_json(stored: str) -> dict:
    raw = (stored or "").strip()
    if not raw:
        return {}
    try:
        return normalize_parameter_schema(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return {}


def collect_parameter_refs(value, found=None):
    """Return sorted unique parameter names referenced via ``$name`` strings."""
    if found is None:
        found = set()
    if isinstance(value, str):
        key = _match_param_ref(value)
        if key:
            found.add(key)
    elif isinstance(value, list):
        for item in value:
            collect_parameter_refs(item, found)
    elif isinstance(value, dict):
        for item in value.values():
            collect_parameter_refs(item, found)
    return sorted(found)


def substitute_parameters(value, parameters: dict):
    """Recursively replace ``$param`` strings with values from ``parameters``."""
    if isinstance(value, str):
        key = _match_param_ref(value)
        if key:
            if key not in parameters:
                raise ValueError(f"missing parameter value for '${key}'")
            return parameters[key]
        return value
    if isinstance(value, list):
        return [substitute_parameters(item, parameters) for item in value]
    if isinstance(value, dict):
        return {k: substitute_parameters(v, parameters) for k, v in value.items()}
    return value


def merge_apply_parameters(defaults: dict, overrides: dict) -> dict:
    """Merge arrangement defaults with apply-time overrides (overrides win)."""
    merged = dict(defaults or {})
    for key, val in (overrides or {}).items():
        name = str(key or "").strip()
        if not name:
            continue
        merged[name] = val
    return merged


def coerce_parameter_value(name: str, raw, spec: dict):
    """Coerce a raw apply-time value to the schema type."""
    ptype = (spec or {}).get("type") or "text"
    if raw is None:
        return None
    if ptype == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("true", "1", "yes", "on")
        return bool(raw)
    if ptype == "number":
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return raw
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"parameter '{name}' must be a number")
    # text, principal, sha256 (stored value is already a hash string from UI)
    s = str(raw).strip()
    if not s:
        return ""
    return s


def prepare_apply_parameters(schema: dict, defaults: dict, overrides: dict, steps: list) -> dict:
    """Validate and merge parameters for one apply run."""
    schema = schema or {}
    defaults = normalize_parameters(defaults)
    overrides = normalize_parameters(overrides)
    merged = merge_apply_parameters(defaults, overrides)

    required = set(collect_parameter_refs(steps))
    for key, spec in schema.items():
        if spec.get("required"):
            required.add(key)

    coerced = {}
    for key in required:
        spec = schema.get(key, {"type": "text"})
        if key not in merged or merged.get(key) in (None, ""):
            if spec.get("required") or key in collect_parameter_refs(steps):
                raise ValueError(f"missing required parameter '{key}'")
            continue
        coerced[key] = coerce_parameter_value(key, merged.get(key), spec)

    # Include optional provided values too.
    for key, val in merged.items():
        if key in coerced:
            continue
        if val in (None, ""):
            continue
        spec = schema.get(key, {"type": "text"})
        coerced[key] = coerce_parameter_value(key, val, spec)

    return coerced
