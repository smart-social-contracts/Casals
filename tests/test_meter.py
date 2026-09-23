"""Progress percentage and ETA for scripts/deploy.sh."""

import io
import time

from casals_cli.meter import ProgressMeter, format_duration
from casals_cli.ship import parse_args


def test_format_duration():
    assert format_duration(0) == "0s"
    assert format_duration(59.4) == "59s"
    assert format_duration(90) == "1m 30s"
    assert format_duration(3661) == "1h 01m"


def test_percent_and_eta_from_completed_units():
    buf = io.StringIO()
    meter = ProgressMeter(stream=buf)
    meter.t0 = time.monotonic() - 30
    meter.add(4, "files")
    meter.advance(1, "files")
    assert meter.percent() == 25
    eta = meter.eta_seconds()
    assert eta is not None and 80 <= eta <= 100
    assert "ETA" in meter.line()
    assert "[ 25%]" in meter.line()


def test_eta_is_unknown_until_a_unit_finishes():
    meter = ProgressMeter(stream=io.StringIO())
    meter.add(8, "installing backend wasm")
    assert meter.percent() == 0
    assert meter.eta_seconds() is None
    assert "estimating" in meter.line()


def test_finish_fills_the_bar():
    buf = io.StringIO()
    meter = ProgressMeter(stream=buf)
    meter.add(4, "files")
    meter.advance(1)
    meter.finish("deployed")
    assert meter.percent() == 100
    assert meter.eta_seconds() == 0.0


def test_parse_targets():
    both = parse_args([])
    assert both.backend and both.frontend
    assert both.identity is None
    fe = parse_args(["frontend", "--identity", "prod-session-20h", "--skip-build"])
    assert fe.frontend and not fe.backend
    assert fe.identity == "prod-session-20h"
    assert fe.skip_build
    be = parse_args(["backend"])
    assert be.backend and not be.frontend


def test_pick_live_session_skips_expired():
    from casals_cli.ship import pick_live_session

    now = 1_000_000_000_000_000_000
    hour = 3_600_000_000_000_000
    # expired, inside the 5-minute margin, and one that lasts
    chosen = pick_live_session(
        [("prod-session", now - hour), ("prod-session-12h", now + 60_000_000_000), ("prod-session-20h", now + 10 * hour)],
        now,
    )
    assert chosen == "prod-session-20h"
    assert pick_live_session([("prod-session", now - hour)], now) is None
