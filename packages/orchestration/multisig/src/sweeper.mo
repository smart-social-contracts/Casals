import Cycles "mo:core/Cycles";
import Principal "mo:core/Principal";
import Runtime "mo:core/Runtime";

/// Tiny helper reinstalled onto a doomed canister so it can deposit its
/// own cycles to the Casals treasury. The governance multisig is the
/// controller; Casals is never added.
persistent actor {
  public shared ({ caller }) func sweep(treasury : Principal, amount : Nat) : async () {
    if (not Principal.isController(caller)) { Runtime.trap("only a controller may sweep") };
    let ic00 = actor ("aaaaa-aa") : actor {
      deposit_cycles : shared { canister_id : Principal } -> async ();
    };
    await (with cycles = amount) ic00.deposit_cycles({ canister_id = treasury });
  };

  public query func cycles_balance() : async Nat {
    Cycles.balance();
  };
};
