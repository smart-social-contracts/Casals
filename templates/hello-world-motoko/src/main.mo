import Debug "mo:core/Debug";
import Error "mo:core/Error";
import Iter "mo:core/Iter";
import Map "mo:core/Map";
import Text "mo:core/Text";

/// Hello-world canister — Motoko.
///
/// The smallest useful canister, plus the one thing a stand's backend must be
/// able to do in a Casals orchestra: **vote on its own Baton**. The stand's
/// Baton lists this canister as a commander (`$stand.backend` in the sheet), so
/// an upgrade the team proposes only runs once this canister approves it —
/// "the team advises, the user decides". `actions`, `approve` and `reject` are
/// that vote, driven from the stand's frontend. There is no `propose`:
/// proposing is the team's side (`casals upgrade --wasm`).
///
/// Config (the Baton's id, set by the sheet's `config` item) is a stable map
/// so it survives the upgrades it is there to approve.
persistent actor {
  let version = "1.2.0";

  // `greet` is an update (not a query) so its log line is recorded in the
  // canister log — the IC does not log non-replicated query calls.
  public func greet(name : Text) : async Text {
    Debug.print("greet called with name=" # name);
    return "Hello, " # name # "! (v" # version # ")";
  };

  public query func health_check() : async Text {
    "{\"status\":\"ok\"}";
  };

  // ── Config ──────────────────────────────────────────────────────────────────
  // The same text-JSON protocol product canisters use, so a sheet's `config`
  // items converge against it:
  //   {"method": "set_canister_config_json", "args": {"baton": "$stand.baton"},
  //    "converged_when": {"query": "get_canister_config_json", "equals_args": true}}
  // Values are flat: a JSON object of string fields. That is all the sheet
  // sends this template, and it keeps the parser a few lines.

  let config = Map.empty<Text, Text>();

  /// `{"a": "x", "b": "y"}` → [("a","x"), ("b","y")]. Flat string fields only;
  /// anything else is ignored.
  func parseFlatObject(json : Text) : [(Text, Text)] {
    var out : [(Text, Text)] = [];
    let body = Text.trim(Text.trim(json, #char ' '), #char '{');
    for (pair in Text.split(Text.trimEnd(body, #char '}'), #char ',')) {
      let parts = Iter.toArray(Text.split(pair, #char ':'));
      if (parts.size() >= 2) {
        let key = Text.trim(Text.trim(parts[0], #char ' '), #char '\"');
        // a canister id may not contain ':', so re-joining is safe here
        var rest = parts[1];
        var i = 2;
        while (i < parts.size()) { rest := rest # ":" # parts[i]; i += 1 };
        let value = Text.trim(Text.trim(rest, #char ' '), #char '\"');
        if (key != "") out := Iter.toArray(Iter.concat(out.vals(), [(key, value)].vals()));
      };
    };
    out;
  };

  func configJson() : Text {
    var fields : [Text] = [];
    for ((k, v) in Map.entries(config)) {
      fields := Iter.toArray(Iter.concat(fields.vals(), ["\"" # k # "\":\"" # v # "\""].vals()));
    };
    "{" # Text.join(fields.vals(), ",") # "}";
  };

  public func set_canister_config_json(args : Text) : async Text {
    for ((k, v) in parseFlatObject(args).vals()) {
      Map.add(config, Text.compare, k, v);
    };
    "{\"success\":true}";
  };

  public query func get_canister_config_json() : async Text {
    configJson();
  };

  // ── Baton client ────────────────────────────────────────────────────────────
  // The Baton speaks text-JSON. This canister is one of its commanders, so
  // calls made *from here* carry the vote; the frontend talks to this
  // canister, never to the Baton, for anything that changes state.

  type Baton = actor {
    list_actions : shared query () -> async Text;
    submit_approval : shared Text -> async Text;
    reject_action : shared Text -> async Text;
  };

  func err(message : Text) : Text {
    "{\"ok\":false,\"error\":\"" # Text.replace(message, #char '\"', "'") # "\"}";
  };

  func baton() : ?Baton {
    switch (Map.get(config, Text.compare, "baton")) {
      case (?id) {
        if (id == "") return null;
        ?(actor (id) : Baton);
      };
      case null null;
    };
  };

  let noBaton = "no baton configured: set_canister_config_json {\"baton\": <canister id>}";

  /// The Baton's action list (`list_actions`), as the Baton returns it.
  /// An update, not a query: a query cannot make inter-canister calls.
  public func actions() : async Text {
    switch (baton()) {
      case null err(noBaton);
      case (?b) {
        try { await b.list_actions() } catch (e) { err("baton call failed: " # Error.message(e)) };
      };
    };
  };

  /// Cast this stand's vote for a pending Baton action (`submit_approval`).
  /// Once the Baton's quorum is met it runs the pipeline on its own.
  public func approve(action_id : Text) : async Text {
    Debug.print("approve action " # action_id);
    switch (baton()) {
      case null err(noBaton);
      case (?b) {
        try { await b.submit_approval(Text.trim(action_id, #char ' ')) } catch (e) { err("baton call failed: " # Error.message(e)) };
      };
    };
  };

  /// Reject a pending Baton action (`reject_action`).
  public func reject(action_id : Text) : async Text {
    Debug.print("reject action " # action_id);
    switch (baton()) {
      case null err(noBaton);
      case (?b) {
        try { await b.reject_action(Text.trim(action_id, #char ' ')) } catch (e) { err("baton call failed: " # Error.message(e)) };
      };
    };
  };
};
