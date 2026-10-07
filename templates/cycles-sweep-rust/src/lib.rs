use candid::{CandidType, Principal};

#[derive(CandidType)]
struct DepositCyclesArgs {
    canister_id: Principal,
}

/// Transfer cycles from this canister to ``dest`` via the management canister.
/// Controllers only: anyone else could send the balance anywhere.
#[ic_cdk::update]
async fn sweep(dest: Principal, amount: u64) {
    if !ic_cdk::api::is_controller(&ic_cdk::caller()) {
        ic_cdk::trap("only a controller may sweep");
    }
    let args = DepositCyclesArgs { canister_id: dest };
    let _: ((),) = ic_cdk::api::call::call_with_payment128(
        Principal::management_canister(),
        "deposit_cycles",
        (args,),
        amount as u128,
    )
    .await
    .expect("deposit_cycles failed");
}

ic_cdk::export_candid!();
