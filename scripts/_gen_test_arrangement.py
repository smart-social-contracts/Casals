#!/usr/bin/env python3
"""One-shot generator for seed/arrangements/test.json (full-fidelity realms test
environment). Mirrors examples/demo/realm{1,2,3}/manifest.json from the realms
repo. Re-run if the manifests change, then delete — the JSON is the artifact."""
import json
import os

FILE_REGISTRY = "uq2mu-kaaaa-aaaah-avqcq-cai"
MARKETPLACE = "2wldc-niaaa-aaaad-qlxga-cai"
NETWORK = "test"

TEST_FLAGS = {
    "test_mode": True,
    "skip_authentication": True,
    "ii_bypass": True,
    "user_self_registration": True,
    "demo_data": True,
    "skip_terms": True,
    "skip_passport_zkproof": True,
}

# Common extension prefix shared by all three realms.
_COMMON = [
    "public_dashboard", "member_dashboard", "admin_dashboard", "census",
    "realm_settings", "extensions_manager", "welcome", "voting", "vault",
    "codex_viewer", "passport_verification", "notifications", "metrics",
    "package_manager", "system_info", "task_monitor", "justice_litigation",
    "land_registry", "market_place", "erd_explorer", "zone_selector",
    "llm_chat", "hello_world", "test_bench", "demo_simulator",
    "managed_services",
]

REALMS = [
    {
        "codex": "dominion",
        "backend": "ku6cv-2iaaa-aaaab-agrpa-cai",
        "frontend": "2enu3-byaaa-aaaad-qlxfa-cai",
        "name": "Dominion",
        "manifesto": (
            "A projection of where the world is likely heading: centralized, "
            "technocratic governance where algorithms, surveillance, and "
            "simulated consent replace genuine democratic agency."
        ),
        "welcome_message": (
            "Welcome to Dominion. Efficient governance through algorithmic "
            "oversight. Your compliance ensures collective prosperity."
        ),
        "extensions": _COMMON + ["mundus_explorer", "department_docs"],
    },
    {
        "codex": "agora",
        "backend": "rnghe-haaaa-aaaak-qyxyq-cai",
        "frontend": "pqwsi-vyaaa-aaaau-agrbq-cai",
        "name": "Agora",
        "manifesto": (
            "A direct democracy funded by monthly membership dues. Citizens "
            "verify their identity via ZK passport and pay monthly bills to stay "
            "active. Active members submit and vote on proposals to change the "
            "codex code, fund services, or redistribute wealth as social "
            "welfare. Every transaction is recorded in real-time double-entry "
            "accounting."
        ),
        "welcome_message": (
            "Welcome to Agora! Join our transparent, participatory community "
            "where every citizen has a voice and governance serves the people."
        ),
        "extensions": _COMMON + ["role_manager", "access_manager", "mundus_explorer"],
    },
    {
        "codex": "syntropia",
        "backend": "m2wv3-uaaaa-aaaah-quoiq-cai",
        "frontend": "2dmsp-maaaa-aaaad-qlxfq-cai",
        "name": "Syntropia",
        "manifesto": (
            "A possible evolutionary future enabled by smart social contracts "
            "and Realms GOS, where governance becomes protocol-based, adaptive, "
            "and freely chosen through a competitive ecosystem of AI-powered "
            "governors."
        ),
        "welcome_message": (
            "Welcome to Syntropia. Experience adaptive, protocol-based "
            "governance where AI-powered systems compete to serve your evolving "
            "needs."
        ),
        "extensions": _COMMON + ["mundus_explorer"],
    },
]


def build_steps():
    steps = []
    for r in REALMS:
        be = r["backend"]
        # 1. Runtime config: test flags + infra ids + this realm's frontend, so
        #    extension installs copy their frontend bundles to the right asset
        #    canister. Must precede demo_simulator so its initialize() sees
        #    demo_data and auto-enables persona generation.
        steps.append({
            "target": be,
            "method": "set_canister_config_json",
            "args": {
                "network": NETWORK,
                "file_registry_canister_id": FILE_REGISTRY,
                "frontend_canister_id": r["frontend"],
                "marketplace_canister_id": MARKETPLACE,
                "test_flags": TEST_FLAGS,
            },
        })
        # 2. Runtime identity (was baked into the per-realm WASM via manifest.json).
        steps.append({
            "target": be,
            "method": "update_realm_config",
            "args": {
                "name": r["name"],
                "manifesto": r["manifesto"],
                "welcome_message": r["welcome_message"],
            },
        })
        # 3. The realm's codex package.
        steps.append({
            "target": be,
            "method": "install_codex_from_registry",
            "args": {
                "registry_canister_id": FILE_REGISTRY,
                "codex_id": r["codex"],
                "version": None,
                "run_init": True,
            },
        })
        # 4..n. Every extension from the realm manifest. demo_simulator (in the
        #       list) auto-activates persona generation from the runtime
        #       demo_data flag set in step 1.
        for ext in r["extensions"]:
            steps.append({
                "target": be,
                "method": "install_extension_from_registry",
                "args": {
                    "registry_canister_id": FILE_REGISTRY,
                    "ext_id": ext,
                    "version": None,
                },
            })
    return steps


def main():
    steps = build_steps()
    doc = {
        "$comment": (
            "Full-fidelity test-environment arrangement for the realms mundus, "
            "applied AFTER a sheet deploy/reinstall to bring the 3 demo realms "
            "(Dominion, Agora, Syntropia) up fully configured and usable. Per "
            "realm, in order: (1) set_canister_config_json — runtime test flags + "
            "file_registry/frontend/marketplace ids (config that used to be baked "
            "in at build time); (2) update_realm_config — name/manifesto/welcome "
            "(identity that used to come from the baked manifest.json); (3) "
            "install_codex_from_registry — the realm's codex; (4..n) "
            "install_extension_from_registry for every extension in the realm's "
            "manifest. demo_simulator auto-generates rich personas on its schedule "
            "from the runtime demo_data flag (step order matters). Targets are raw "
            "canister ids from realms/deployment-descriptors/test-mundus-layered.yml. "
            "Apply in batches (apply_arrangement offset/limit) — this is ~"
            + str(len(steps)) + " steps. Regenerate via "
            "scripts/_gen_test_arrangement.py."
        ),
        "name": "test",
        "description": (
            "Realms test environment: per-realm runtime flags + identity + codex "
            "+ full extension set (demo_simulator auto-seeds personas)."
        ),
        "active": True,
        "parameters": {
            "network": NETWORK,
            "file_registry_canister_id": FILE_REGISTRY,
            "marketplace_canister_id": MARKETPLACE,
            "test_flags": TEST_FLAGS,
        },
        "steps": steps,
    }
    out = os.path.join(os.path.dirname(__file__), "..", "seed", "arrangements", "test.json")
    out = os.path.abspath(out)
    with open(out, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"wrote {out}: {len(steps)} steps")


if __name__ == "__main__":
    main()
