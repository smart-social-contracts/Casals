import chain

# stand -> {step, canister, done, total, at}: the latest step of a stand's pending build, for orchestra_get.
# Heap only, like the rounds that write it: a build resumed after an upgrade writes it again in its next round.
builds = {}


def note(stand, step, canister, done, total):
    builds[stand] = {"step": step, "canister": canister, "done": done, "total": total, "at": chain.now()}


def of(stand):
    return builds.get(stand, {})


def clear(stand):
    builds.pop(stand, None)
