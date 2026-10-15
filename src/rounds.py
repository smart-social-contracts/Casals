import chain

WATCHDOG = 900  # seconds; a round that traps never reaches its except, so a timer fails it instead
STEPS = {}
queue = []
armed = []
running = {}


def step(kind, fail, pending):
    def wrap(fn):
        STEPS[kind] = (fn, fail, pending)
        return fn
    return wrap


def schedule(kind, name):
    if (kind, name) not in queue:
        queue.append((kind, name))
    if not armed:
        armed.append(chain.set_timer(1, tick))


def tick():
    armed.clear()
    kind, name = queue.pop(0)
    run, fail, _ = STEPS[kind]
    running[(kind, name)] = token = chain.now()
    chain.set_timer(WATCHDOG, lambda: watchdog(kind, name, token))
    try:
        if not (yield from run(name)):
            queue.append((kind, name))
    except Exception as e:
        fail(name, str(e))
    running.pop((kind, name), None)
    if queue:
        schedule(*queue[0])


def watchdog(kind, name, token):
    if running.get((kind, name)) != token:
        return
    del running[(kind, name)]
    STEPS[kind][1](name, f"the {kind} round trapped (e.g. the instruction limit); fix the cause, then retry")
    if queue:
        schedule(*queue[0])


def resume(*steps):
    """`steps` names each @step function: Basilisk imports lazily, so that is what registers them in STEPS."""
    for kind, (_, _, pending) in STEPS.items():
        for name in pending():
            schedule(kind, name)
