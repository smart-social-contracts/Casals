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
