import Debug "mo:core/Debug";

persistent actor {
  public query func health_check() : async Text {
    "{\"status\":\"ok\",\"version\":2}";
  };

  public func greet(name : Text) : async Text {
    Debug.print("greet v2: " # name);
    "Hello v2, " # name # "!";
  };
};
