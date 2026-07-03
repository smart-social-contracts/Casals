import Debug "mo:core/Debug";

persistent actor {
  public query func health_check() : async Text {
    "{\"status\":\"ok\"}";
  };

  public func greet(name : Text) : async Text {
    Debug.print("greet: " # name);
    "Hello, " # name # "!";
  };
};
