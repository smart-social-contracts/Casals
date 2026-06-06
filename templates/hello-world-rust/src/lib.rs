// `greet` is an update (not a query) so its log line is recorded in the canister
// log — the IC does not log non-replicated query calls.
#[ic_cdk::update]
fn greet(name: String) -> String {
    ic_cdk::println!("greet called with name={}", name);
    format!("Hello, {}!", name)
}

ic_cdk::export_candid!();
