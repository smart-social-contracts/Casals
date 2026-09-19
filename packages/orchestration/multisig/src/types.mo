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
    #DestroyStand : { casals_backend : Principal; stand : Text };
    #DestroyCanister : { casals_backend : Principal; canister_id : Principal };
    #DestroyCanisters : { canister_ids : [Principal]; casals_backend : Principal };
    #ApplySheet : {
      casals_backend : Principal;
      plan_hash : Text;
      confirm_destructive : Bool;
      max_items : Nat;
    };
    #CallCanister : { canister : Principal; method : Text; arg_json : Text };
    /// Upgrade a canister this multisig controls with a module streamed from
    /// the ``casals-wasms`` store (``store``/``key``). ``sha256`` pins the
    /// module the committee approved; ``install_chunked_code`` refuses anything
    /// else. ``wasm_memory_keep`` is the EOP switch (Motoko yes, Rust/Basilisk no).
    #UpgradeCanister : {
      canister_id : Principal;
      store : Principal;
      key : Text;
      sha256 : Blob;
      arg : Blob;
      wasm_memory_keep : Bool;
    };
  };

  public type ProposalStatus = { #pending; #executed; #rejected; #failed; #expired };

  public type Proposal = {
    id : Nat;
    action : BatonAction;
    proposed_by : Principal;
    approvals : [Principal];
    status : ProposalStatus;
    created_at : Timestamp;
    expires_at : Timestamp;
    result : ?Text;
  };

  public type AuditEvent = { at : Timestamp; kind : Text; detail : Text };
  public type Result = { #ok; #err : Text };
  public type ExecuteResult = { #ok : ?Text; #err : Text };
};
