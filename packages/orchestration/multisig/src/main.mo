import Array "mo:core/Array";
import Nat "mo:core/Nat";
import Principal "mo:core/Principal";
import Text "mo:core/Text";
import Time "mo:core/Time";

import Types "types";

persistent actor {
  type Capability = Types.Capability;
  type BatonAction = Types.BatonAction;
  type ProposalStatus = Types.ProposalStatus;
  type Proposal = Types.Proposal;
  type AuditEvent = Types.AuditEvent;
  type Result = Types.Result;
  type Timestamp = Types.Timestamp;

  private stable var signers : [Principal] = [];
  private stable var threshold : Nat = 1;
  private stable var proposal_expiry_secs : Nat = 604800;
  private stable var next_proposal_id : Nat = 0;
  private stable var proposal_entries : [(Nat, Proposal)] = [];
  private stable var event_log : [AuditEvent] = [];

  private func now() : Timestamp { Time.now() };

  private func log(kind : Text, detail : Text) {
    event_log := Array.concat(event_log, [{ at = now(); kind; detail }]);
  };

  private func isSigner(p : Principal) : Bool {
    Array.find<Principal>(signers, func(x) { x == p }) != null;
  };

  private func validateSigners(th : Nat, ss : [Principal]) : Result {
    let m = ss.size();
    if (m == 0) { return #err("signer set cannot be empty") };
    if (th == 0 or th > m) { return #err("threshold must satisfy 1 <= n <= m") };
    #ok;
  };

  public shared ({ caller }) func configure(
    init_signers : [Principal],
    init_threshold : Nat,
    expiry_secs : Nat,
  ) : async Result {
    if (signers.size() != 0) { return #err("already configured") };
    switch (validateSigners(init_threshold, init_signers)) {
      case (#ok) {};
      case (#err(e)) { return #err(e) };
    };
    signers := init_signers;
    threshold := init_threshold;
    proposal_expiry_secs := expiry_secs;
    log("configured", Nat.toText(init_threshold));
    #ok;
  };

  private func setProposal(id : Nat, p : Proposal) {
    var out : [(Nat, Proposal)] = [];
    var found = false;
    for ((k, v) in proposal_entries.vals()) {
      if (k == id) { out := Array.concat(out, [(k, p)]); found := true }
      else { out := Array.concat(out, [(k, v)]) };
    };
    if (not found) { out := Array.concat(out, [(id, p)]) };
    proposal_entries := out;
  };

  private func getProposal(id : Nat) : ?Proposal {
    for ((k, v) in proposal_entries.vals()) {
      if (k == id) return ?v;
    };
    null;
  };

  private func hasApproved(p : Proposal, signer : Principal) : Bool {
    Array.find<Principal>(p.approvals, func(x) { x == signer }) != null;
  };

  private func tryExecute(p : Proposal) : async Proposal {
    if (p.approvals.size() < threshold) return p;
    switch (p.status) {
      case (#pending) {};
      case (_) { return p };
    };
    let executed = await executeAction(p.action);
    switch (executed) {
      case (#ok) {
        log("executed", "proposal " # Nat.toText(p.id));
        { p with status = #executed };
      };
      case (#err(e)) {
        log("execute_failed", e);
        { p with status = #rejected };
      };
    };
  };

  private func encodeCaps(caps : [Capability]) : Text {
    var first = true;
    var out = "[";
    for (c in caps.vals()) {
      if (not first) { out := out # "," };
      out := out # "\"" # c # "\"";
      first := false;
    };
    out # "]";
  };

  private func executeAction(action : BatonAction) : async Result {
    switch (action) {
      case (#UpgradeBaton(a)) {
        let ic = actor ("aaaaa-aa") : actor {
          install_code : shared {
            mode : { #install; #reinstall; #upgrade };
            canister_id : Principal;
            wasm_module : Blob;
            arg : Blob;
          } -> async ();
        };
        try {
          await ic.install_code({
            mode = #upgrade;
            canister_id = a.baton_id;
            wasm_module = a.wasm_module;
            arg = a.arg;
          });
          #ok;
        } catch (_) { #err("install_code failed") };
      };
      case (#UpdateBatonSettings(a)) {
        let ic = actor ("aaaaa-aa") : actor {
          update_settings : shared {
            canister_id : Principal;
            settings : {
              controllers : ?[Principal];
              compute_allocation : ?Nat;
              memory_allocation : ?Nat;
              freezing_threshold : ?Nat;
            };
          } -> async ();
        };
        try {
          await ic.update_settings({
            canister_id = a.baton_id;
            settings = {
              controllers = if (a.add_controllers.size() + a.remove_controllers.size() == 0) null else ?a.add_controllers;
              compute_allocation = null;
              memory_allocation = null;
              freezing_threshold = null;
            };
          });
          #ok;
        } catch (_) { #err("update_settings failed") };
      };
      case (#SetCanisterControllers(a)) {
        let ic = actor ("aaaaa-aa") : actor {
          update_settings : shared {
            canister_id : Principal;
            settings : {
              controllers : ?[Principal];
              compute_allocation : ?Nat;
              memory_allocation : ?Nat;
              freezing_threshold : ?Nat;
            };
          } -> async ();
        };
        try {
          await ic.update_settings({
            canister_id = a.canister_id;
            settings = {
              controllers = ?a.controllers;
              compute_allocation = null;
              memory_allocation = null;
              freezing_threshold = null;
            };
          });
          #ok;
        } catch (_) { #err("update_settings failed") };
      };
      case (#AddCommander(a)) {
        let baton = actor (Principal.toText(a.baton_id)) : actor {
          add_commander : shared Text -> async Text;
        };
        let payload = "{\"principal\":\"" # Principal.toText(a.commander) # "\",\"capabilities\":" #
          encodeCaps(a.capabilities) # "}";
        try {
          ignore await baton.add_commander(payload);
          #ok;
        } catch (_) { #err("add_commander failed") };
      };
      case (#RemoveCommander(a)) {
        let baton = actor (Principal.toText(a.baton_id)) : actor {
          remove_commander : shared Text -> async Text;
        };
        try {
          ignore await baton.remove_commander(Principal.toText(a.commander));
          #ok;
        } catch (_) { #err("remove_commander failed") };
      };
      case (#SetPolicy(p)) {
        let baton = actor (Principal.toText(p.baton_id)) : actor {
          set_commander_policy : shared Text -> async Text;
        };
        try {
          ignore await baton.set_commander_policy(p.policy_json);
          log("set_policy", p.policy_json);
          #ok;
        } catch (_) { #err("set_commander_policy failed") };
      };
      case (#ManageSigners(a)) {
        var ss = signers;
        for (p in a.add.vals()) {
          if (Array.find<Principal>(ss, func(x) { x == p }) == null) {
            ss := Array.concat(ss, [p]);
          };
        };
        var filtered : [Principal] = [];
        for (p in ss.vals()) {
          var removed = false;
          for (r in a.remove.vals()) { if (r == p) { removed := true } };
          if (not removed) { filtered := Array.concat(filtered, [p]) };
        };
        let th = switch (a.new_threshold) { case (?t) t; case null threshold };
        switch (validateSigners(th, filtered)) {
          case (#ok) { signers := filtered; threshold := th; #ok };
          case (#err(e)) { #err(e) };
        };
      };
    };
  };

  public query func default_proposal_expiry_secs() : async Nat {
    proposal_expiry_secs;
  };

  public shared ({ caller }) func propose(action : BatonAction, expiry_secs : ?Nat) : async Nat {
    assert (isSigner(caller));
    let id = next_proposal_id;
    next_proposal_id += 1;
    let secs = switch (expiry_secs) {
      case (?s) {
        assert (s > 0);
        s;
      };
      case null { proposal_expiry_secs };
    };
    let p : Proposal = {
      id;
      action;
      proposed_by = caller;
      approvals = [caller];
      status = #pending;
      created_at = now();
      expires_at = now() + secs * 1_000_000_000;
    };
    log("proposed", Nat.toText(id));
    let executed = await tryExecute(p);
    setProposal(id, executed);
    id;
  };

  public shared ({ caller }) func approve(proposal_id : Nat) : async Result {
    assert (isSigner(caller));
    switch (getProposal(proposal_id)) {
      case null { #err("unknown proposal") };
      case (?p) {
        if (p.status != #pending) return #err("proposal not pending");
        if (now() > p.expires_at) {
          setProposal(proposal_id, { p with status = #expired });
          return #err("proposal expired");
        };
        if (hasApproved(p, caller)) return #err("already approved");
        let updated = {
          p with approvals = Array.concat(p.approvals, [caller]);
        };
        log("approved", Nat.toText(proposal_id));
        let executed = await tryExecute(updated);
        setProposal(proposal_id, executed);
        #ok;
      };
    };
  };

  public shared ({ caller }) func reject(proposal_id : Nat) : async Result {
    assert (isSigner(caller));
    switch (getProposal(proposal_id)) {
      case null { #err("unknown proposal") };
      case (?p) {
        if (p.status != #pending) return #err("proposal not pending");
        setProposal(proposal_id, { p with status = #rejected });
        log("rejected", Nat.toText(proposal_id));
        #ok;
      };
    };
  };

  public query func get_proposal(proposal_id : Nat) : async ?Proposal {
    getProposal(proposal_id);
  };

  public query func list_proposals() : async [Proposal] {
    var out : [Proposal] = [];
    for ((_, p) in proposal_entries.vals()) {
      out := Array.concat(out, [p]);
    };
    out;
  };

  public query func list_signers() : async { signers : [Principal]; threshold : Nat } {
    { signers; threshold };
  };

  public query func list_events() : async [AuditEvent] {
    event_log;
  };
};
