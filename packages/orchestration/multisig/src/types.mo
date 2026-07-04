import Nat "mo:core/Nat";
import Principal "mo:core/Principal";
import Text "mo:core/Text";
import Time "mo:core/Time";

module {
  public type Capability = Text;
  public type Timestamp = Time.Time;

  public type BatonAction = {
    #UpgradeBaton : { baton_id : Principal; wasm_module : Blob; arg : Blob };
    #UpdateBatonSettings : {
      baton_id : Principal;
      add_controllers : [Principal];
      remove_controllers : [Principal];
    };
    #SetCanisterControllers : { canister_id : Principal; controllers : [Principal] };
    #AddCommander : { baton_id : Principal; commander : Principal; capabilities : [Capability] };
    #RemoveCommander : { baton_id : Principal; commander : Principal };
    #SetPolicy : { baton_id : Principal; policy_json : Text };
    #ManageSigners : { add : [Principal]; remove : [Principal]; new_threshold : ?Nat };
  };

  public type ProposalStatus = { #pending; #executed; #rejected; #expired };

  public type Proposal = {
    id : Nat;
    action : BatonAction;
    proposed_by : Principal;
    approvals : [Principal];
    status : ProposalStatus;
    created_at : Timestamp;
    expires_at : Timestamp;
  };

  public type AuditEvent = { at : Timestamp; kind : Text; detail : Text };
  public type Result = { #ok; #err : Text };
};
