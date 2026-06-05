import Debug "mo:core/Debug";

persistent actor {
  // `greet` is an update (not a query) so its log line is recorded in the
  // canister log — the IC does not log non-replicated query calls.
  public func greet(name : Text) : async Text {
    Debug.print("greet called with name=" # name);
    return "Hello, " # name # "!";
  };
};
