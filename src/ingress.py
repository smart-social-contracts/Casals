"""Pure ingress decision for ``inspect_message``. No canister state."""

from helpers import ANONYMOUS


def accept_ingress(method_name: str, caller: str) -> bool:
    """Whether ``inspect_message`` should call ``ic.accept_message``.

    ``http_request_update`` is accepted from every caller, including the
    anonymous principal. Every other method rejects anonymous and accepts
    everyone else. Not calling accept rejects the ingress. Queries are not
    inspected.
    """
    if method_name == "http_request_update":
        return True
    if caller == ANONYMOUS:
        return False
    return True
