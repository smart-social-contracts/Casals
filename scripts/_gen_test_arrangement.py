#!/usr/bin/env python3
"""Generator for the realms test-environment arrangements:

  - seed/arrangements/test.json       full fidelity: 3 realms, every manifest
                                      extension (~93 steps). Mirrors
                                      examples/demo/realm{1,2,3}/manifest.json.
  - seed/arrangements/test-lite.json  fast iteration: 1 realm (Dominion), a
                                      handful of core extensions (~8 steps).

Re-run if the manifests change. The JSON files are the committed artifacts."""
import json
import os

# Lite arrangement: one realm + a few high-signal extensions, for fast end-to-end
# iteration of the reinstall→arrangement-apply chain without ~93 mainnet calls.
LITE_EXTENSIONS = [
    "public_dashboard",
    "member_dashboard",
    "demo_simulator",
    "hello_world",
    "llm_chat",
]

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


def build_steps(realms, ext_filter=None):
    steps = []
    for r in realms:
        be = r["backend"]
        extensions = r["extensions"]
        if ext_filter is not None:
            # Preserve the realm's own ordering; keep only the requested set.
            extensions = [e for e in ext_filter if e in r["extensions"]]
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
        for ext in extensions:
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


def _write(filename, name, description, comment, steps, active):
    doc = {
        "$comment": comment,
        "name": name,
        "description": description,
        "active": active,
        "parameters": {
            "network": NETWORK,
            "file_registry_canister_id": FILE_REGISTRY,
            "marketplace_canister_id": MARKETPLACE,
            "test_flags": TEST_FLAGS,
        },
        "steps": steps,
    }
    out = os.path.join(os.path.dirname(__file__), "..", "seed", "arrangements", filename)
    out = os.path.abspath(out)
    with open(out, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"wrote {out}: {len(steps)} steps")


def main():
    full = build_steps(REALMS)
    _write(
        "test.json", "test",
        ("Realms test environment: per-realm runtime flags + identity + codex + "
         "full extension set (demo_simulator auto-seeds personas)."),
        ("Full-fidelity test-environment arrangement for the realms mundus, "
         "applied AFTER a sheet deploy/reinstall to bring the 3 demo realms "
         "(Dominion, Agora, Syntropia) up fully configured and usable. Per realm, "
         "in order: (1) set_canister_config_json — runtime test flags + "
         "file_registry/frontend/marketplace ids (config that used to be baked in "
         "at build time); (2) update_realm_config — name/manifesto/welcome "
         "(identity that used to come from the baked manifest.json); (3) "
         "install_codex_from_registry — the realm's codex; (4..n) "
         "install_extension_from_registry for every extension in the realm's "
         "manifest. demo_simulator auto-generates rich personas on its schedule "
         "from the runtime demo_data flag (step order matters). Targets are raw "
         "canister ids from realms/deployment-descriptors/test-mundus-layered.yml. "
         "Apply in batches (apply_arrangement offset/limit) — ~" + str(len(full)) +
         " steps. Regenerate via scripts/_gen_test_arrangement.py."),
        full, active=True,
    )

    # Lite: one realm + a few core extensions, for fast iteration. Not active by
    # default (the full `test` arrangement is the active one); activate explicitly
    # with `casals arrangement activate test-lite` when iterating.
    lite = build_steps(REALMS[:1], ext_filter=LITE_EXTENSIONS)
    _write(
        "test-lite.json", "test-lite",
        ("Lightweight realms test arrangement: 1 realm (Dominion) + core "
         "extensions (" + ", ".join(LITE_EXTENSIONS) + ") for fast iteration."),
        ("Fast-iteration variant of `test`: just Dominion and a handful of core "
         "extensions (" + ", ".join(LITE_EXTENSIONS) + "), ~" + str(len(lite)) +
         " steps, to exercise the reinstall→arrangement-apply chain end-to-end "
         "without ~93 mainnet calls. Same per-realm step shape as `test`. Not "
         "active by default; `casals arrangement activate test-lite` to use it. "
         "Regenerate via scripts/_gen_test_arrangement.py."),
        lite, active=False,
    )


if __name__ == "__main__":
    main()
