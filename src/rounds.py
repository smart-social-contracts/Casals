import chain

STEPS = {}
queue = []
armed = []


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
    try:
        if not (yield from run(name)):
            queue.append((kind, name))
    except Exception as e:
        fail(name, str(e))
    if queue:
        schedule(*queue[0])


def resume():
    for kind, (_, _, pending) in STEPS.items():
        for name in pending():
            schedule(kind, name)
