import Cycles "mo:core/Cycles";
import Principal "mo:core/Principal";

/// Tiny helper reinstalled onto a doomed canister so it can deposit its
/// own cycles to the Casals treasury. The governance multisig is the
/// controller; Casals is never added.
persistent actor {
  public func sweep(treasury : Principal, amount : Nat) : async () {
    let ic00 = actor ("aaaaa-aa") : actor {
      deposit_cycles : shared { canister_id : Principal } -> async ();
    };
    await (with cycles = amount) ic00.deposit_cycles({ canister_id = treasury });
  };

  public query func cycles_balance() : async Nat {
    Cycles.balance();
  };
};
