import chain

WATCHDOG = 900  # seconds; a round still running by then trapped, or its call never came back
LOST = 300  # seconds; the timer is set for 1 s, so one that has not run by then was dropped
STEPS = {}
queue = []
armed = []  # when the pending timer was set
running = {}


def step(kind, fail, pending):
    def wrap(fn):
        STEPS[kind] = (fn, fail, pending)
        return fn
    return wrap


def schedule(kind, name):
    if (kind, name) not in queue:
        queue.append((kind, name))
    kick()


def kick():
    """Arm the timer when work is queued and no timer is pending and no round is running.

    Every update call runs this, not only new work: the IC drops a timer it cannot run (the
    conductor out of cycles, say) without telling the canister, and only a later call can notice."""
    now = chain.now()
    for (kind, name), started in list(running.items()):
        if now - started > WATCHDOG:
            watchdog(kind, name, started)
    if armed and now - armed[0] > LOST:
        armed.clear()
    if queue and not armed and not running:
        chain.set_timer(1, tick)
        armed.append(now)


def tick():
    armed.clear()
    if not queue or running:
        return  # a timer set after one was presumed lost; the running round re-arms when it ends
    kind, name = queue.pop(0)
    run, fail, _ = STEPS[kind]
    running[(kind, name)] = token = chain.now()
    chain.set_timer(WATCHDOG, lambda: watchdog(kind, name, token))
    # A trap undoes the message back to its last await. Past this one it only undoes the round,
    # which the watchdog then fails; before it, it would undo the lines above as well.
    yield from chain.commit()
    try:
        if not (yield from run(name)) and (kind, name) not in queue:
            queue.append((kind, name))
    except Exception as e:
        fail(name, str(e))
    if running.get((kind, name)) == token:
        del running[(kind, name)]
    kick()


def watchdog(kind, name, token):
    if running.get((kind, name)) != token:
        return
    del running[(kind, name)]
    try:
        STEPS[kind][1](name, f"the {kind} round trapped (e.g. the instruction limit); fix the cause, then retry")
    except Exception:
        pass  # what the round was for is gone; the rest of the queue still has to run
    kick()


def resume(*steps):
    """`steps` names each @step function: Basilisk imports lazily, so that is what registers them in STEPS."""
    for kind, (_, _, pending) in STEPS.items():
        for name in pending():
            schedule(kind, name)
