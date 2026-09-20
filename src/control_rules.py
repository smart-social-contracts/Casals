"""Controller-change safety rules, shared by the planner (the day-one build)
and the imperative endpoints (`set_canister_controllers`, `sync_controllers`).

Host-Python testable: no basilisk import."""

from __future__ import annotations


def lockout_error(name: str, live: list, desired: list) -> str | None:
    """A change that removes controllers must leave at least one."""
    if not set(live) - set(desired):
        return None
    if not desired:
        return f"{name}: controller removal would leave no valid controller"
    return None


def controller_change_error(name: str, live: list, desired: list, *, self_id: str,
                            multisig_id: str = "", baton_id: str = "") -> str | None:
    """Why an imperative controller change on ``name`` must be refused, or None.

    - it may not leave the canister without a controller (``lockout_error``);
    - it may not leave the canister with no controller the orchestra can reach —
      Casals, the governance multisig or the stand's baton — because from then
      on only whoever else is listed can ever change it again;
    - it may not drop the baton from a member the baton controls: the baton is
      how the stand's commanders upgrade that member.
    Callers pass ``force`` to override the last two (never the first)."""
    err = lockout_error(name, live, desired)
    if err:
        return err
    live_s, desired_s = set(live), set(desired)
    if not desired_s:
        return f"{name}: controller list may not be empty"
    reach = {p for p in (self_id, multisig_id, baton_id) if p}
    if reach and live_s & reach and not desired_s & reach:
        who = ", ".join(sorted(desired_s))
        return (f"{name}: {who} would be the only controller(s); neither Casals, the multisig nor the "
                f"stand's baton could reach the canister again (force=true to do it anyway)")
    if baton_id and baton_id in live_s and baton_id not in desired_s:
        return (f"{name}: dropping baton {baton_id} from the controllers would take the member out of its "
                f"stand's upgrade pipeline (force=true to do it anyway)")
    return None
