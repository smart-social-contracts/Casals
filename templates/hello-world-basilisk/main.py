"""Hello-world canister — Basilisk (Python on the IC).

The smallest useful Basilisk canister: a single query that greets a name.
Used as a Casals catalog template to demonstrate creating a Python-runtime
stand. Build with `make build-templates`.
"""

from basilisk import query, text


@query
def greet(name: text) -> text:
    return "Hello, " + name + "!"
