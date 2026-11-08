"""The real chain.gather: src/chain.py loaded over a stub of the Basilisk runtime, since the rest of the suite
swaps chain for fake_chain, whose gather is not this code."""

import importlib.util
import pathlib
import sys
import types

import pytest

CHAIN = pathlib.Path(__file__).resolve().parents[2] / "src" / "chain.py"


@pytest.fixture
def runtime(monkeypatch):
    """The real chain module and the IC around it: a clock, and the timers gather started and has not run yet."""
    ic = types.SimpleNamespace(seconds=0, timers=[], started=[])
    ic.time = lambda: ic.seconds * 1_000_000_000
    ic.set_timer = lambda delay, callback: ic.timers.append(callback) or ic.started.append(callback)
    basilisk = types.ModuleType("basilisk")
    basilisk.Duration, basilisk.Principal, basilisk.ic = (lambda secs: secs), object, ic
    management = types.ModuleType("basilisk.canisters.management")
    management.management_canister = types.SimpleNamespace(raw_rand=lambda: "raw_rand")
    for name, stub in (("basilisk", basilisk), ("basilisk.canisters", types.ModuleType("basilisk.canisters")),
                       ("basilisk.canisters.management", management)):
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("real_chain", CHAIN)
    ic.chain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ic.chain)
    return ic


def run(ic, gens):
    """gather(gens) as the IC runs it: each time it awaits, a second passes and every started task takes one step."""
    gathering, tasks = ic.chain.gather(gens), []
    try:
        for _ in range(2 * ic.chain.GATHER_LIMIT):  # a gather that never ends fails here instead of hanging the suite
            next(gathering)
            ic.seconds += 1
            tasks += [callback() for callback in ic.timers]
            ic.timers.clear()
            for task in list(tasks):
                try:
                    next(task)
                except StopIteration:
                    tasks.remove(task)
    except StopIteration as done:
        return done.value
    raise AssertionError("gather was still waiting after twice GATHER_LIMIT")


def steps(count, result=None):
    for _ in range(count):
        yield
    return result


def results_held_by(task):
    """The `outcomes` dict a started task writes its result to (the timers keep the callbacks, and so it)."""
    return dict(zip(task.__code__.co_freevars, (cell.cell_contents for cell in task.__closure__)))["outcomes"]


def test_results_come_back_in_order_whenever_the_tasks_finish(runtime):
    assert run(runtime, [steps(3, "a"), steps(1, "b"), steps(2, "c")]) == ["a", "b", "c"]
    assert all(results_held_by(task) == {} for task in runtime.started)


def test_the_first_error_is_raised_once_every_task_has_finished(runtime):
    finished = []

    def fails():
        yield
        raise ValueError("first")

    def slow():
        yield from steps(3)
        finished.append("slow")

    with pytest.raises(ValueError, match="first"):
        run(runtime, [fails(), slow()])
    assert finished == ["slow"]


def test_a_task_that_never_finishes_fails_the_wait_after_the_limit(runtime):
    def never():
        while True:
            yield

    with pytest.raises(Exception, match="1 of 2 tasks never finished"):
        run(runtime, [steps(1, "done"), never()])
    assert runtime.seconds > runtime.chain.GATHER_LIMIT
    assert all(results_held_by(task) == {} for task in runtime.started)
