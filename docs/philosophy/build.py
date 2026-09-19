#!/usr/bin/env python3
"""Generate index.html from steps.md for the Casals design-rationale deck."""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
MD_PATH = DIR / "steps.md"
HTML_PATH = DIR / "index.html"

# One entry per scene of the rationale walk. The diagram, the progress rail,
# the side panel and the progress rail all derive from
# this list, so the numbering can never drift.
STEPS: list[dict] = [
    {
        "key": "app",
        "rail": "Application",
        "title": "An application is several canisters",
        "points": [
            "On the Internet Computer an application is not one canister: a <strong>backend</strong>, a <strong>frontend</strong>, often more.",
            "Each canister has <strong>IC controllers</strong>. Only they can install code, upgrade, top up cycles, stop or delete it.",
        ],
        "therefore": "Someone has to hold the controller keys.",
    },
    {
        "key": "control",
        "rail": "Multisig",
        "title": "Someone must control them — a multisig",
        "points": [
            "The usual answer is an <strong>SNS</strong>: token, swap, public DAO. Not every project wants that for its infrastructure.",
            "The alternative is a <strong>multisig</strong>: it becomes the IC controller of every canister.",
            "Every install, upgrade and top-up is a proposal that <strong>N of M people sign</strong>.",
            "Safe, simple, sufficient — for a handful of canisters.",
        ],
        "therefore": "A committee holds the keys. It works until the fleet grows.",
    },
    {
        "key": "grow-x",
        "rail": "More canisters",
        "title": "The application grows",
        "points": [
            "Features add canisters: index, assets, workers.",
            "One instance is now a <strong>list of canisters</strong> that must be upgraded together.",
            "The committee still signs each change, one canister at a time.",
        ],
        "therefore": "Growth along one axis: more canisters per instance.",
    },
    {
        "key": "grow-y",
        "rail": "More users",
        "title": "Every user needs their own set",
        "points": [
            "Multi-tenant: each user gets its own instance — backend, frontend and the rest.",
            "A <strong>list of lists</strong> — users × canisters. A second axis of growth.",
            "The committee now signs for every canister of every user.",
        ],
        "therefore": "Instances multiply. But not every canister belongs to one user.",
    },
    {
        "key": "shared",
        "rail": "Shared",
        "title": "Some canisters serve everyone",
        "points": [
            "A registry of users, an installer, a shared ledger: <strong>infrastructure</strong>, not a user's instance.",
            "They exist once, and change on a different rhythm than user instances.",
            "The committee now signs for all of it. A multisig is <strong>people</strong>: it cannot top up cycles at 3 am, or roll out two hundred upgrades one signature at a time.",
        ],
        "therefore": "Two kinds of canisters, two axes of growth — and a multisig that is impractical for day-to-day operations.",
    },
    {
        "key": "conductor",
        "rail": "Conductor",
        "title": "Name the grid, hand it to a conductor",
        "points": [
            "<strong>Section</strong> — a group with a shared role: Infra, Users. <strong>Stand</strong> — one instance inside it: the shared infrastructure, user1, user2. The whole grid is the <strong>orchestra</strong>.",
            "Day-to-day operations need an operator that is a canister, not a committee: <strong>casals-backend</strong>, the conductor, becomes the IC controller of every orchestra canister.",
            "<strong>casals-frontend</strong> is the team's console: orchestra tree, upgrades, cycles, WASM catalog. The multisig controls only these two — highest authority, rarely needed.",
            "<strong>Commanders</strong>: team members with scoped permissions per section or stand.",
        ],
        "therefore": "The team can operate. The user has to trust the team.",
    },
    {
        "key": "baton",
        "rail": "Baton",
        "title": "The baton: the user decides",
        "points": [
            "The team can now upgrade a user's canisters. The user needs both <strong>protection</strong> from that power and <strong>autonomy</strong> over their own instance.",
            "A <strong>baton</strong> per stand. The team proposes and <strong>advises</strong> — recommends a version, prepares the rollout — but the <strong>user decides</strong>: approve, or not.",
            "Once approved, the baton runs the upgrade across all of that user's canisters as one unit — snapshot, stop, install, verify, roll back.",
        ],
        "therefore": "Team advises and operates · user decides · multisig remains the backstop.",
    },
    {
        "key": "services",
        "rail": "Registry & cycles",
        "title": "Two more pieces: a WASM registry and a treasury",
        "points": [
            "<strong>casals-wasms</strong> — a certified file registry: chunked upload, <strong>sha256 computed on-chain</strong>, pinned per release. Every install <strong>streams the WASM from it</strong>; only authorized WASMs, and the conductor verifies <code>module_hash</code> afterwards. user1 and user2 provably run the same build.",
            "<strong>Cycles</strong> — the conductor holds a <strong>native treasury</strong> that the multisig funds. A cycle policy per section, stand or canister; an on-chain autopilot (or an off-chain monitor paying from the same treasury) refills before anything runs dry. Every top-up is an audited event.",
        ],
        "therefore": "Code and cycles both flow from Casals — on-chain, verifiable, audited.",
    },
]

PHASES = [s["key"] for s in STEPS]
N_STEPS = len(STEPS)

MULTISIG_NOTES: dict[str, str] = {
    "grow-x": "signs every install · upgrade · top-up",
    "grow-y": "… for every canister of every user",
    "shared": "… and for the shared infrastructure",
    "conductor": "controls Casals only · highest authority",
    "baton": "highest authority · backstop",
    "services": "funds the treasury · signs nothing else",
}

SECTION_TITLES: dict[str, tuple[str, str | None]] = {
    "steps": ("Architecture design rationale", None),
}

TITLE_MAIN = "Casals"
TITLE_SUBTITLE_HTML = (
    "<strong>General-purpose canister lifecycle orchestrator for the Internet Computer</strong>"
    " — built for <strong>managed multi-tenant IC deployments with shared upgrade governance</strong>"
)


def _phase_css() -> str:
    """Per-phase CSS generated from STEPS (side panel, multisig notes)."""
    out: list[str] = []
    for step in STEPS:
        key = step["key"]
        sel = f'.rationale-slide[data-phase="{key}"]'
        out.append(f"{sel} .step-detail[data-for=\"{key}\"] {{ display: flex; }}")
        out.append(f"{sel} .ms-note[data-for=\"{key}\"] {{ display: block; }}")
    return "\n    ".join(out)


CSS = """
    :root {
      --gray-50: #fafafa;
      --gray-100: #f0f0f0;
      --gray-200: #e5e5e5;
      --gray-300: #d4d4d4;
      --gray-400: #a3a3a3;
      --gray-500: #737373;
      --gray-600: #525252;
      --gray-700: #404040;
      --gray-800: #262626;
      --gray-900: #171717;
      --text: var(--gray-900);
      --muted: var(--gray-600);
      --border: var(--gray-200);
      --panel: #ffffff;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    html, body { height: 100%; overflow: hidden; }

    body {
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      background: var(--panel);
      color: var(--text);
      min-height: 100vh;
      min-height: 100dvh;
      display: flex;
      flex-direction: column;
    }

    .deck {
      width: 100%;
      height: 100vh;
      height: 100dvh;
      background: var(--panel);
      padding: 1.25rem 2.5rem 1rem;
      display: flex;
      flex-direction: column;
      flex: 1;
    }

    .viewport {
      flex: 1;
      min-height: 0;
      position: relative;
      user-select: text;
      -webkit-user-select: text;
      cursor: default;
    }

    .dots { user-select: none; -webkit-user-select: none; }

    .page {
      position: absolute;
      inset: 0;
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      z-index: 0;
      transition: opacity 0.45s cubic-bezier(0.4, 0, 0.2, 1), visibility 0.45s;
    }

    .page.active {
      opacity: 1;
      visibility: visible;
      pointer-events: auto;
      z-index: 2;
    }

    .page-step {
      position: absolute;
      inset: 0;
      opacity: 0;
      pointer-events: none;
      z-index: 0;
      transition: opacity 0.45s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .page-step.active { opacity: 1; pointer-events: auto; z-index: 1; }

    .arch-box {
      width: 100%;
      height: 100%;
      border: 2px solid var(--gray-800);
      border-radius: 8px;
      padding: 2rem 2.2rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }

    .box-header {
      border-bottom: 2px solid var(--gray-900);
      padding-bottom: 0.75rem;
      flex-shrink: 0;
    }

    .box-header h2 {
      font-size: 2rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }

    .box-header p {
      font-size: 0.95rem;
      color: var(--muted);
      margin-top: 0.25rem;
    }

    .box-body {
      flex: 1;
      display: flex;
      align-items: center;
    }

    .box-body ul {
      list-style: none;
      font-size: 1.05rem;
      line-height: 1.7;
      color: var(--gray-700);
      width: 100%;
    }

    .box-body li { padding-left: 1rem; position: relative; margin-bottom: 0.2rem; }
    .box-body li strong { color: var(--text); font-weight: 650; }
    .box-body li em { color: var(--gray-500); font-style: normal; font-size: 0.9em; }
    .box-body code {
      font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
      font-size: 0.92em;
      background: var(--gray-100);
      padding: 0.08em 0.32em;
      border-radius: 3px;
    }


    .dots {
      display: flex;
      gap: 0.35rem;
      justify-content: center;
      margin-top: 0.45rem;
      flex-wrap: wrap;
    }

    .dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--gray-200);
      border: none;
      padding: 0;
      cursor: pointer;
      transition: background 0.3s, transform 0.3s;
    }

    .dot.active { background: var(--gray-900); transform: scale(1.2); }

    .arch-box.casals-box-slide {
      position: relative;
      width: min(64rem, 58%);
      height: auto;
      flex: none;
      padding: 2.7rem 3rem;
      gap: 1.8rem;
    }

    .page-step:has(.casals-box-slide):not(:has(.title-slide)):not(:has(.section-divider-slide)):not(:has(.wide-slide)) {
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .arch-box.casals-box-slide .box-body ul.bullets-hollow > li {
      padding-left: 2rem;
      margin-bottom: 0.65rem;
    }

    .arch-box.casals-box-slide .box-body ul.bullets-hollow > li::before {
      content: "";
      position: absolute;
      left: 0.35rem;
      top: 0.68em;
      width: 0.45rem;
      height: 0.45rem;
      border: 1.5px solid var(--gray-600);
      border-radius: 50%;
      background: transparent;
    }

    /* Title */
    .arch-box.title-slide {
      width: min(92%, 48rem);
      height: auto;
      padding: 3rem 2.75rem;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 0;
    }

    .page-step:has(.title-slide) {
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .title-slide .title-main {
      font-size: 2.75rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      line-height: 1.15;
      margin-bottom: 0.55rem;
    }

    .title-slide .title-sub {
      font-size: 1.2rem;
      color: var(--gray-500);
      line-height: 1.5;
      max-width: 38rem;
      margin: 0.25rem 0 0;
    }

    .title-slide .title-sub strong { color: var(--gray-800); font-weight: 650; }

    .num-badge {
      flex-shrink: 0;
      width: 1.45rem;
      height: 1.45rem;
      border-radius: 50%;
      border: 1.5px solid var(--gray-500);
      font-size: 0.72rem;
      font-weight: 700;
      color: var(--gray-700);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transform: translateY(0.18rem);
      background: #fff;
    }

    /* Section divider */
    .arch-box.section-divider-slide {
      width: min(92%, 46rem);
      height: auto;
      padding: 3rem 2.75rem;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
    }

    .page-step:has(.section-divider-slide) {
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .section-divider-slide .box-header { border-bottom: none; padding-bottom: 0; }

    .section-divider-slide .box-header h2 {
      font-size: 2.75rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }

    .section-divider-slide .box-header p {
      margin-top: 0.55rem;
      font-size: 1.15rem;
      color: var(--gray-500);
    }

    .arch-box.wide-slide { width: min(78rem, 94%); }

    .page-step:has(.rationale-slide),
    .page-step:has(.authority-slide) {
      display: flex;
      align-items: stretch;
      justify-content: center;
    }

    .arch-box.rationale-slide,
    .arch-box.authority-slide {
      width: 100%;
      height: 100%;
      padding: 1.25rem 1.6rem 1.1rem;
      gap: 0.7rem;
    }

    .arch-box.rationale-slide .box-header,
    .arch-box.authority-slide .box-header {
      padding-bottom: 0.55rem;
    }

    .arch-box.rationale-slide .box-header h2,
    .arch-box.authority-slide .box-header h2 {
      font-size: 1.55rem;
    }

    /* Progress rail */
    .step-rail {
      position: relative;
      display: flex;
      align-items: flex-start;
      margin-top: 0.7rem;
      padding: 0 0.5rem;
    }

    .step-rail::before {
      content: "";
      position: absolute;
      left: calc(100% / 16);
      right: calc(100% / 16);
      top: 0.78rem;
      height: 1px;
      background: var(--gray-300);
    }

    .rail-step {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.28rem;
      position: relative;
    }

    .rail-num {
      width: 1.6rem;
      height: 1.6rem;
      border-radius: 50%;
      border: 1.5px solid var(--gray-400);
      background: #fff;
      color: var(--gray-500);
      font-size: 0.72rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.3s, color 0.3s, border-color 0.3s;
    }

    .rail-label {
      font-size: 0.6rem;
      font-weight: 650;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gray-400);
      text-align: center;
      line-height: 1.2;
      transition: color 0.3s;
    }

    .rail-step.is-done .rail-num {
      background: var(--gray-200);
      border-color: var(--gray-400);
      color: var(--gray-700);
    }

    .rail-step.is-current .rail-num {
      background: var(--gray-900);
      border-color: var(--gray-900);
      color: #fff;
    }

    .rail-step.is-current .rail-label { color: var(--gray-900); font-weight: 700; }

    /* Split body: diagram left, explanation right */
    .rationale-split {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
      gap: 1.25rem;
    }

    .rationale {
      min-height: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 0.45rem;
      padding: 0.5rem 0;
    }

    .step-panel {
      min-height: 0;
      border-left: 1px solid var(--gray-200);
      padding: 0.35rem 0 0.35rem 1.4rem;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }

    .step-detail {
      display: none;
      flex-direction: column;
      gap: 0.75rem;
    }

    .step-kicker {
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--gray-500);
    }

    .step-kicker .step-big {
      display: block;
      font-size: 2.6rem;
      font-weight: 700;
      letter-spacing: -0.03em;
      line-height: 1;
      color: var(--gray-300);
      margin-bottom: 0.3rem;
    }

    .step-title {
      font-size: 1.45rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      line-height: 1.25;
      color: var(--gray-900);
    }

    .step-points {
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }

    .step-points li {
      position: relative;
      padding-left: 1.35rem;
      font-size: 0.98rem;
      line-height: 1.5;
      color: var(--gray-700);
    }

    .step-points li::before {
      content: "";
      position: absolute;
      left: 0.1rem;
      top: 0.55em;
      width: 0.45rem;
      height: 0.45rem;
      border: 1.5px solid var(--gray-600);
      border-radius: 50%;
    }

    .step-points strong { color: var(--gray-900); font-weight: 650; }

    .step-therefore {
      border-top: 1px solid var(--gray-300);
      padding-top: 0.65rem;
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }

    .step-therefore-label {
      font-size: 0.64rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--gray-500);
    }

    .step-therefore-text {
      font-size: 1.02rem;
      font-weight: 650;
      color: var(--gray-900);
      line-height: 1.4;
    }

    /* Diagram pieces — hidden until their phase */
    .choice-row,
    .multisig-pill,
    .casals-row,
    .arrow-down,
    .axis-x,
    .axis-y,
    .sec-label,
    .stand-label,
    .can.extra,
    .can.baton,
    .section[data-sec="users"],
    .stand[data-stand="shared"],
    .can.more-can,
    .commanders,
    .orch-label,
    .ms-note,
    .approver,
    .shared-note {
      display: none;
    }

    .rationale-slide[data-phase] .stand-label.app-label { display: none; }

    .shared-note {
      font-weight: 600;
      color: var(--gray-500);
      font-size: 0.64rem;
    }

    .choice-row { gap: 0.75rem; align-items: stretch; }

    .choice {
      border: 1.5px solid var(--gray-400);
      border-radius: 6px;
      padding: 0.55rem 1.1rem;
      min-width: 11rem;
      text-align: center;
      background: #fff;
    }

    .choice-name { font-size: 0.92rem; font-weight: 700; color: var(--gray-800); }

    .choice-note {
      margin-top: 0.2rem;
      font-size: 0.68rem;
      font-weight: 600;
      color: var(--gray-500);
      line-height: 1.3;
    }

    .choice.faded { opacity: 0.4; border-style: dashed; }
    .choice.pick { border-color: var(--gray-900); background: var(--gray-100); }

    .multisig-pill {
      border: 1.5px solid var(--gray-700);
      border-radius: 999px;
      padding: 0.45rem 1.25rem;
      font-size: 0.95rem;
      font-weight: 650;
      background: var(--gray-100);
      text-align: center;
      min-width: 13rem;
    }

    .ms-note {
      font-size: 0.68rem;
      font-weight: 600;
      color: var(--gray-500);
      margin-top: 0.1rem;
    }

    .arrow-down {
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--gray-800);
      line-height: 1;
      user-select: none;
    }

    .arrow-label {
      display: block;
      font-size: 0.58rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gray-500);
      margin-top: 0.15rem;
    }

    .casals-row { align-items: center; gap: 0.65rem; }

    .casals-box {
      border: 1.5px solid var(--gray-800);
      border-radius: 6px;
      padding: 0.45rem 0.95rem;
      background: #fff;
      text-align: center;
    }

    .casals-box-name { font-size: 0.95rem; font-weight: 700; }

    .casals-box-note {
      font-size: 0.62rem;
      font-weight: 600;
      color: var(--gray-500);
      margin-top: 0.08rem;
    }

    .commanders { flex-direction: column; align-items: center; gap: 0.2rem; }

    .commanders-label {
      font-size: 0.58rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--gray-500);
    }

    .commanders-dots { display: flex; gap: 0.28rem; }

    .commander {
      width: 0.78rem;
      height: 0.78rem;
      border-radius: 50%;
      border: 1.5px solid var(--gray-700);
    }

    .commander.c1 { background: var(--gray-800); }
    .commander.c2 { background: var(--gray-500); }
    .commander.c3 { background: var(--gray-200); }

    .orchestra {
      width: auto;
      max-width: 100%;
      border: 1.5px dashed transparent;
      border-radius: 6px;
      padding: 0.15rem;
      display: flex;
      flex-direction: column;
      align-items: stretch;
      gap: 0.55rem;
      background: transparent;
    }

    .orch-label {
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gray-600);
      text-align: center;
    }

    .sections {
      display: flex;
      gap: 0.7rem;
      align-items: flex-start;
      justify-content: center;
    }

    .section {
      border: 1.5px solid transparent;
      border-radius: 6px;
      padding: 0.15rem;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }

    .sec-label {
      font-size: 0.74rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gray-600);
      padding-bottom: 0.2rem;
      border-bottom: 1px solid var(--gray-200);
    }

    .stand {
      border: 1px solid var(--gray-300);
      border-radius: 4px;
      padding: 0.4rem 0.5rem;
      background: #fff;
    }

    .stand-label {
      font-size: 0.8rem;
      font-weight: 650;
      color: var(--text);
      margin-bottom: 0.18rem;
    }

    .approver {
      font-weight: 600;
      color: var(--gray-500);
      font-size: 0.64rem;
    }

    .cans { display: flex; flex-wrap: wrap; gap: 0.22rem; }

    .can {
      font-size: 0.76rem;
      font-weight: 600;
      color: var(--gray-700);
      border: 1px solid var(--gray-300);
      border-radius: 999px;
      padding: 0.18rem 0.5rem;
      background: var(--gray-50);
      line-height: 1.2;
    }

    .can.baton {
      border-color: var(--gray-900);
      border-width: 1.5px;
      background: #fff;
      font-weight: 700;
      color: var(--text);
    }

    .axis-x, .axis-y {
      font-size: 0.62rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gray-500);
    }

    .axis-x { text-align: center; }
    .axis-y { writing-mode: vertical-rl; transform: rotate(180deg); align-self: center; }

    .grow-wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.45rem;
      max-width: 100%;
    }

    /* Phases 1–3 (app, control, grow-x): a single application, boxed with a dashed frame */
    .rationale-slide[data-phase="app"] .stand[data-stand="app"],
    .rationale-slide[data-phase="control"] .stand[data-stand="app"],
    .rationale-slide[data-phase="grow-x"] .stand[data-stand="app"] {
      border: 1.5px dashed var(--gray-400);
      background: transparent;
      padding: 0.55rem 0.8rem 0.65rem;
    }
    .rationale-slide[data-phase="app"] .stand-label.app-label,
    .rationale-slide[data-phase="control"] .stand-label.app-label,
    .rationale-slide[data-phase="grow-x"] .stand-label.app-label {
      display: block;
      text-align: center;
      font-size: 0.66rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gray-500);
      margin-bottom: 0.35rem;
    }
    .rationale-slide[data-phase="app"] .can { border-color: var(--gray-800); }

    /* Phase 2 — control: SNS vs multisig */
    .rationale-slide[data-phase="control"] .choice-row { display: flex; }
    .rationale-slide[data-phase="control"] .arrow-down.choice-arrow { display: block; }

    /* Phase 3 — grow-x: multisig pill appears, app gains canisters */
    .rationale-slide[data-phase="grow-x"] .multisig-pill { display: block; }
    .rationale-slide[data-phase="grow-x"] .arrow-down.ms-arrow { display: block; }
    .rationale-slide[data-phase="grow-x"] .can.extra { display: inline-block; border-color: var(--gray-800); }
    .rationale-slide[data-phase="grow-x"] .axis-x { display: block; }
    .rationale-slide[data-phase="grow-x"] .stand[data-stand="app"] { border-color: var(--gray-900); }

    /* Phase 4 — grow-y: only user instances, no shared canisters yet */
    .rationale-slide[data-phase="grow-y"] .multisig-pill { display: block; }
    .rationale-slide[data-phase="grow-y"] .arrow-down.ms-arrow { display: block; }
    .rationale-slide[data-phase="grow-y"] .section[data-sec="infra"] { display: none; }
    .rationale-slide[data-phase="grow-y"] .section[data-sec="users"] { display: flex; }
    .rationale-slide[data-phase="grow-y"] .can.more-can { display: inline-block; }
    .rationale-slide[data-phase="grow-y"] .axis-x,
    .rationale-slide[data-phase="grow-y"] .axis-y { display: block; }
    .rationale-slide[data-phase="grow-y"] .stand-label { display: block; color: var(--gray-500); font-weight: 600; }
    .rationale-slide[data-phase="grow-y"] .section[data-sec="users"] .stand:not([data-stand="more"]) { border-color: var(--gray-900); }

    /* Phases 5–7: shared infrastructure beside the users; the single-app stand is gone */
    .rationale-slide[data-phase="shared"] .multisig-pill,
    .rationale-slide[data-phase="conductor"] .multisig-pill,
    .rationale-slide[data-phase="services"] .multisig-pill { display: block; }
    .rationale-slide[data-phase="baton"] .multisig-pill { display: block; }
    .rationale-slide[data-phase="shared"] .arrow-down.ms-arrow,
    .rationale-slide[data-phase="conductor"] .arrow-down.ms-arrow,
    .rationale-slide[data-phase="services"] .arrow-down.ms-arrow { display: block; }
    .rationale-slide[data-phase="baton"] .arrow-down.ms-arrow { display: block; }
    .rationale-slide[data-phase="shared"] .section[data-sec="users"],
    .rationale-slide[data-phase="conductor"] .section[data-sec="users"],
    .rationale-slide[data-phase="services"] .section[data-sec="users"] { display: flex; }
    .rationale-slide[data-phase="baton"] .section[data-sec="users"] { display: flex; }
    .rationale-slide[data-phase="shared"] .stand[data-stand="app"],
    .rationale-slide[data-phase="conductor"] .stand[data-stand="app"],
    .rationale-slide[data-phase="services"] .stand[data-stand="app"] { display: none; }
    .rationale-slide[data-phase="baton"] .stand[data-stand="app"] { display: none; }
    .rationale-slide[data-phase="shared"] .stand[data-stand="shared"],
    .rationale-slide[data-phase="conductor"] .stand[data-stand="shared"],
    .rationale-slide[data-phase="services"] .stand[data-stand="shared"] { display: block; }
    .rationale-slide[data-phase="baton"] .stand[data-stand="shared"] { display: block; }
    .rationale-slide[data-phase="shared"] .can.more-can,
    .rationale-slide[data-phase="conductor"] .can.more-can,
    .rationale-slide[data-phase="services"] .can.more-can { display: inline-block; }
    .rationale-slide[data-phase="baton"] .can.more-can { display: inline-block; }
    .rationale-slide[data-phase="shared"] .stand-label,
    .rationale-slide[data-phase="conductor"] .stand-label,
    .rationale-slide[data-phase="services"] .stand-label { display: block; }
    .rationale-slide[data-phase="baton"] .stand-label { display: block; }
    .rationale-slide[data-phase="shared"] .sections,
    .rationale-slide[data-phase="conductor"] .sections,
    .rationale-slide[data-phase="services"] .sections { align-items: stretch; }
    .rationale-slide[data-phase="baton"] .sections { align-items: stretch; }
    .rationale-slide[data-phase="shared"] .section { min-width: 12.5rem; }

    /* Phase 5 — shared: the new thing is the shared stand */
    .rationale-slide[data-phase="shared"] .stand[data-stand="shared"] { border-color: var(--gray-900); }
    .rationale-slide[data-phase="shared"] .shared-note { display: inline; }
    .rationale-slide[data-phase="shared"] .axis-x,
    .rationale-slide[data-phase="shared"] .axis-y { display: block; }

    /* Phases 6–7: framed orchestra with section labels */
    .rationale-slide[data-phase="conductor"] .sec-label,
    .rationale-slide[data-phase="conductor"] .orch-label,
    .rationale-slide[data-phase="services"] .sec-label,
    .rationale-slide[data-phase="baton"] .sec-label,
    .rationale-slide[data-phase="services"] .orch-label { display: block; }
    .rationale-slide[data-phase="baton"] .orch-label { display: block; }
    .rationale-slide[data-phase="conductor"] .section,
    .rationale-slide[data-phase="services"] .section,
    .rationale-slide[data-phase="baton"] .section {
      border-color: var(--gray-700);
      padding: 0.5rem 0.6rem;
      min-width: 12.5rem;
    }
    .rationale-slide[data-phase="conductor"] .orchestra,
    .rationale-slide[data-phase="services"] .orchestra,
    .rationale-slide[data-phase="baton"] .orchestra {
      border-color: var(--gray-400);
      background: var(--gray-50);
      padding: 0.65rem 0.75rem;
      min-width: 30rem;
    }

    
    /* Phase 6 — conductor: frame, labels and casals-backend appear together */
    .rationale-slide[data-phase="conductor"] .arrow-down.ca-arrow,
    .rationale-slide[data-phase="services"] .arrow-down.ca-arrow { display: block; }
    .rationale-slide[data-phase="baton"] .arrow-down.ca-arrow { display: block; }
    .rationale-slide[data-phase="conductor"] .casals-row,
    .rationale-slide[data-phase="conductor"] .commanders,
    .rationale-slide[data-phase="services"] .casals-row,
    .rationale-slide[data-phase="baton"] .casals-row,
    .rationale-slide[data-phase="services"] .commanders { display: flex; }
    .rationale-slide[data-phase="baton"] .commanders { display: flex; }
    .rationale-slide[data-phase="conductor"] .casals-box { border-width: 2px; border-color: var(--gray-900); }
    .casals-box.casals-fe { border-style: dashed; border-color: var(--gray-500); background: var(--gray-50); }
    .rationale-slide[data-phase="conductor"] .casals-box.casals-fe { border-color: var(--gray-700); }
    .rationale-slide[data-phase="conductor"] .arrow-label { display: block; }

    /* Phase 7 — baton */
    .rationale-slide[data-phase="services"] .can.baton { display: inline-block; }
    .rationale-slide[data-phase="baton"] .can.baton { display: inline-block; }
    .rationale-slide[data-phase="services"] .approver { display: inline; }
    .rationale-slide[data-phase="baton"] .approver { display: inline; }
    .rationale-slide[data-phase="baton"] .stand[data-stand="u1"],
    .rationale-slide[data-phase="baton"] .stand[data-stand="u2"] { border-color: var(--gray-900); }

    /* Phase 8 — services: WASM registry and cycles treasury */
    .casals-box.casals-wasms, .cycles-tag { display: none; }
    .rationale-slide[data-phase="services"] .casals-box.casals-wasms { display: block; border-width: 2px; border-color: var(--gray-900); }
    .rationale-slide[data-phase="services"] .casals-row { gap: 0.5rem; }
    .rationale-slide[data-phase="services"] .casals-box { padding: 0.4rem 0.7rem; }
    .rationale-slide[data-phase="services"] .casals-box-name { font-size: 0.86rem; white-space: nowrap; }
    .rationale-slide[data-phase="services"] .casals-box-note { font-size: 0.56rem; white-space: nowrap; }
    .rationale-slide[data-phase="services"] .commanders-label { white-space: nowrap; }
    .rationale-slide[data-phase="services"] .casals-box.casals-fe .casals-box-note { display: none; }
    .rationale-slide[data-phase="services"] .cycles-tag { display: inline-block; }
    .cycles-tag {
      margin-top: 0.3rem;
      font-size: 0.6rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--gray-900);
      border: 1.5px solid var(--gray-900);
      border-radius: 999px;
      padding: 0.1rem 0.5rem;
    }
    .rationale-slide[data-phase="services"] .ms-note[data-for="services"] { display: block; }

    /* Authority slide */
    .gov-diagram {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 0.4rem;
      width: min(100%, 38rem);
      margin: 0 auto;
    }

    .gov-tier {
      border: 2px solid var(--gray-700);
      border-radius: 6px;
      padding: 0.7rem 2.6rem;
      background: #fff;
      text-align: center;
      position: relative;
    }

    .gov-tier .num-badge {
      position: absolute;
      left: 0.7rem;
      top: 50%;
      transform: translateY(-50%);
    }

    .gov-tier.gov-multisig { background: var(--gray-100); border-radius: 999px; }
    .gov-tier.gov-baton { background: var(--gray-50); border-color: var(--gray-800); }
    .gov-tier.gov-apps { border-style: dashed; border-color: var(--gray-500); background: var(--gray-50); }

    .gov-tier-label {
      font-size: 0.98rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      color: var(--gray-800);
    }

    .gov-tier-note {
      margin-top: 0.22rem;
      font-size: 0.74rem;
      font-weight: 600;
      line-height: 1.35;
      color: var(--gray-600);
    }

    .gov-arrow {
      text-align: center;
      font-size: 1.05rem;
      line-height: 1;
      color: var(--gray-700);
      flex-shrink: 0;
    }

    .gov-footnote {
      margin-top: 0.35rem;
      text-align: center;
      font-size: 0.78rem;
      color: var(--gray-500);
      font-style: italic;
    }

    /* Examples slides */
    .page-step:has(.examples-slide) {
      display: flex;
      align-items: stretch;
      justify-content: center;
    }

    .arch-box.examples-slide {
      width: 100%;
      height: 100%;
      padding: 1.25rem 1.6rem 1.1rem;
      gap: 0.7rem;
    }

    .arch-box.examples-slide .box-header { padding-bottom: 0.55rem; }
    .arch-box.examples-slide .box-header h2 { font-size: 1.55rem; }

    .ex-body {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 0.6rem;
      width: min(100%, 70rem);
      margin: 0 auto;
    }

    .ex-label {
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--gray-500);
      margin-top: 0.4rem;
    }

    .ex-grid { display: grid; gap: 1.25rem; }
    .ex-grid-2 { grid-template-columns: 1fr 1fr; }

    .ex-card {
      border: 1.5px solid var(--gray-300);
      border-radius: 6px;
      background: #fff;
      padding: 1.35rem 1.5rem;
    }

    .ex-card.ex-today { border-color: var(--gray-800); background: var(--gray-50); }

    .ex-head { display: flex; align-items: baseline; gap: 0.7rem; margin-bottom: 0.55rem; flex-wrap: wrap; }

    .ex-name { font-size: 1.25rem; font-weight: 700; color: var(--gray-900); }

    .ex-pattern .ex-name { margin-bottom: 0.4rem; }

    .ex-tag {
      font-size: 0.74rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--gray-500);
    }

    .ex-text { font-size: 1.02rem; line-height: 1.55; color: var(--gray-700); }
    .ex-text strong { color: var(--gray-900); font-weight: 650; }


    /* Today slide: text + mini orchestra */
    .today-split {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr);
      gap: 2rem;
      align-items: center;
    }

    .today-text { display: flex; flex-direction: column; gap: 1.05rem; }

    .today-h {
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--gray-900);
      margin-bottom: 0.3rem;
    }

    .today-block p,
    .today-block li { font-size: 0.95rem; line-height: 1.5; color: var(--gray-700); }
    .today-block strong { color: var(--gray-900); font-weight: 650; }

    .today-block ul.bullets-hollow { list-style: none; padding: 0; margin: 0; }
    .today-block ul.bullets-hollow > li { position: relative; padding-left: 1.1rem; margin-bottom: 0.35rem; }
    .today-block ul.bullets-hollow > li::before {
      content: "";
      position: absolute;
      left: 0;
      top: 0.5em;
      width: 0.42rem;
      height: 0.42rem;
      border-radius: 50%;
      border: 1.5px solid var(--gray-500);
    }

    .today-diagram {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.3rem;
    }

    .today-diagram .multisig-pill,
    .today-diagram .arrow-down,
    .today-diagram .casals-row,
    .today-diagram .casals-box.casals-wasms,
    .today-diagram .orch-label,
    .today-diagram .sec-label,
    .today-diagram .stand-label { display: block; }
    .today-diagram .casals-row { display: flex; }
    .today-diagram .can.baton { display: inline-block; }
    .today-diagram .casals-box.casals-wasms { border-style: solid; border-color: var(--gray-700); background: #fff; }
    .today-diagram .stand-label em { font-style: normal; font-weight: 700; color: var(--gray-900); }
    .today-diagram .sections { align-items: stretch; }
    .today-diagram .section {
      border-color: var(--gray-700);
      padding: 0.5rem 0.6rem;
      min-width: 11rem;
    }
    .today-diagram .orchestra {
      border-color: var(--gray-400);
      background: var(--gray-50);
      padding: 0.65rem 0.75rem;
    }
    .today-diagram .stand-more { border-style: dashed; }

    @media (max-width: 900px) {
      .deck { padding: 0.75rem 0.85rem 0.65rem; }
      .arch-box.casals-box-slide,
      .arch-box.title-slide,
      .arch-box.section-divider-slide { width: 100%; padding: 1.4rem 1.1rem; }
      .title-slide .title-main,
      .section-divider-slide .box-header h2 { font-size: 1.85rem; }
      .rationale-split { grid-template-columns: 1fr; }
      .step-panel { border-left: none; border-top: 1px solid var(--gray-200); padding: 0.75rem 0 0; }
      .sections { flex-direction: column; }
      .axis-y { display: none !important; }
      .rail-label { display: none; }
    }

    /* generated per-phase rules */
    __PHASE_CSS__
"""

DECK_JS = """
(function () {
  const pages = Array.from(document.querySelectorAll('.page'));
  const dotsEl = document.getElementById('dots');
  let sceneIndex = 0;
  let totalScenes = 0;
  const sceneMap = [];

  function buildSceneMap() {
    sceneMap.length = 0;
    pages.forEach((page, pageIdx) => {
      const steps = parseInt(page.dataset.steps || '1', 10);
      for (let s = 0; s < steps; s++) {
        sceneMap.push({ pageIdx, stepIdx: s });
      }
    });
    totalScenes = sceneMap.length;
  }

  function renderDots() {
    if (!dotsEl) return;
    dotsEl.innerHTML = '';
    for (let i = 0; i < totalScenes; i++) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dot' + (i === sceneIndex ? ' active' : '');
      btn.setAttribute('aria-label', 'Scene ' + (i + 1));
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        window.__goToScene(i + 1);
      });
      dotsEl.appendChild(btn);
    }
  }

  function applyPhase(page, stepIdx) {
    const phases = (page.dataset.phases || '').split(',').filter(Boolean);
    if (!phases.length) return;
    const idx = Math.min(stepIdx, phases.length - 1);
    page.querySelectorAll('[data-phase-host]').forEach((el) => {
      el.setAttribute('data-phase', phases[idx]);
    });
    page.querySelectorAll('.rail-step').forEach((el) => {
      const j = parseInt(el.dataset.idx, 10);
      el.classList.toggle('is-done', j < idx);
      el.classList.toggle('is-current', j === idx);
    });
  }

  function applyScene(idx) {
    if (!sceneMap.length) return 0;
    sceneIndex = Math.max(0, Math.min(idx, totalScenes - 1));
    const { pageIdx, stepIdx } = sceneMap[sceneIndex];

    pages.forEach((page, pi) => {
      const active = pi === pageIdx;
      page.classList.toggle('active', active);
      page.querySelectorAll('.page-step').forEach((step, si) => {
        step.classList.toggle('active', active && si === 0);
      });
      if (active) applyPhase(page, stepIdx);
    });

    renderDots();
    const hash = '#' + (sceneIndex + 1);
    if (location.hash !== hash) {
      history.replaceState(null, '', hash);
    }
    return sceneIndex + 1;
  }

  window.__goToScene = function (n) {
    const target = Math.max(1, Math.min(parseInt(n, 10) || 1, totalScenes));
    return applyScene(target - 1);
  };

  function sceneFromHash() {
    const m = /^#(\\d+)$/.exec(location.hash);
    return m ? parseInt(m[1], 10) : 1;
  }

  buildSceneMap();
  applyScene(sceneFromHash() - 1);

  window.addEventListener('hashchange', () => {
    applyScene(sceneFromHash() - 1);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ') {
      e.preventDefault();
      window.__goToScene(sceneIndex + 2);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      window.__goToScene(sceneIndex);
    } else if (e.key === 'Home') {
      e.preventDefault();
      window.__goToScene(1);
    } else if (e.key === 'End') {
      e.preventDefault();
      window.__goToScene(totalScenes);
    }
  });

  const viewport = document.getElementById('viewport');
  if (viewport) {
    viewport.addEventListener('click', (e) => {
      if (e.target.closest('a, button, .dots')) return;
      window.__goToScene(sceneIndex + 2);
    });
  }
})();
"""


def parse_bullets(lines: list[str]) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    for line in lines:
        m = re.match(r"^(\s*)-\s+(.*)$", line)
        if not m:
            continue
        depth = 1 if len(m.group(1)) >= 4 else 0
        items.append((depth, m.group(2).strip()))
    return items


def render_inline(text: str) -> str:
    parts: list[str] = []
    last = 0
    pattern = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\*(.+?)\*")
    for m in pattern.finditer(text):
        parts.append(html.escape(text[last : m.start()]))
        if m.group(1):
            parts.append(f"<strong>{html.escape(m.group(1))}</strong>")
        elif m.group(2):
            parts.append(f"<code>{html.escape(m.group(2))}</code>")
        else:
            parts.append(f"<em>{html.escape(m.group(3))}</em>")
        last = m.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def render_bullet_list(items: list[tuple[int, str]]) -> str:
    if not items:
        return ""
    lines = ['<ul class="bullets-hollow">']
    for depth, text in items:
        cls = ' class="depth-1"' if depth else ""
        lines.append(f"  <li{cls}>{render_inline(text)}</li>")
    lines.append("</ul>")
    return "\n".join(lines)


def badge(n: int) -> str:
    return f'<span class="num-badge">{n}</span>'


class Slide:
    def __init__(
        self,
        kind: str,
        marker: str = "",
        bullets: list[tuple[int, str]] | None = None,
    ):
        self.kind = kind
        self.marker = marker
        self.bullets = bullets or []

    def render(self) -> tuple[str, int, str]:
        raise NotImplementedError


class TitleSlide(Slide):
    def render(self) -> tuple[str, int, str]:
        body = f"""
            <div class="page-step active">
              <section class="arch-box title-slide casals-box-slide">
                <h1 class="title-main">{html.escape(TITLE_MAIN)}</h1>
                <p class="title-sub">{TITLE_SUBTITLE_HTML}</p>
              </section>
            </div>"""
        return body, 1, ""


class SectionSlide(Slide):
    def render(self) -> tuple[str, int, str]:
        key = self.marker.replace("section ", "").strip()
        title, subtitle = SECTION_TITLES.get(key, (key.replace("-", " ").title(), None))
        sub_html = f"<p>{html.escape(subtitle)}</p>" if subtitle else ""
        body = f"""
            <div class="page-step active">
              <section class="arch-box section-divider-slide">
                <header class="box-header">
                  <h2>{html.escape(title)}</h2>
                  {sub_html}
                </header>
              </section>
            </div>"""
        return body, 1, ""


class ContentSlide(Slide):
    def render(self) -> tuple[str, int, str]:
        body = f"""
            <div class="page-step active">
              <section class="arch-box casals-box-slide">
                <header class="box-header">
                  <h2>{html.escape(self.marker)}</h2>
                </header>
                <div class="box-body">
                  {render_bullet_list(self.bullets)}
                </div>
              </section>
            </div>"""
        return body, 1, ""


class RationaleSlide(Slide):
    def render(self) -> tuple[str, int, str]:
        rail = "".join(
            f'<div class="rail-step" data-idx="{i}">'
            f'<span class="rail-num">{i + 1}</span>'
            f'<span class="rail-label">{html.escape(s["rail"])}</span>'
            f"</div>"
            for i, s in enumerate(STEPS)
        )
        details = []
        for i, s in enumerate(STEPS, 1):
            points = "".join(f"<li>{p}</li>" for p in s["points"])
            details.append(
                f"""
                  <div class="step-detail" data-for="{s['key']}">
                    <div class="step-kicker"><span class="step-big">{i:02d}</span>Step {i} of {N_STEPS}</div>
                    <h3 class="step-title">{html.escape(s['title'])}</h3>
                    <ul class="step-points">{points}</ul>
                    <div class="step-therefore">
                      <span class="step-therefore-label">Therefore</span>
                      <span class="step-therefore-text">{html.escape(s['therefore'])}</span>
                    </div>
                  </div>"""
            )
        ms_notes = "".join(
            f'<small class="ms-note" data-for="{k}">{html.escape(v)}</small>'
            for k, v in MULTISIG_NOTES.items()
        )
        body = f"""
            <div class="page-step active">
              <section class="arch-box rationale-slide wide-slide" data-phase-host data-phase="{PHASES[0]}">
                <header class="box-header">
                  <h2>Why Casals — in eight steps</h2>
                  <div class="step-rail" aria-hidden="true">{rail}</div>
                </header>
                <div class="rationale-split">
                  <div class="rationale">
                    <div class="choice-row">
                      <div class="choice faded">
                        <div class="choice-name">SNS</div>
                        <div class="choice-note">Token · swap · public DAO</div>
                      </div>
                      <div class="choice pick">
                        <div class="choice-name">Multisig</div>
                        <div class="choice-note">N of M signers · IC controller of every canister</div>
                      </div>
                    </div>
                    <div class="arrow-down choice-arrow">↓</div>

                    <div class="multisig-pill">
                      Multisig
                      {ms_notes}
                    </div>
                    <div class="arrow-down ms-arrow">↓</div>

                    <div class="casals-row">
                      <div class="casals-box">
                        <div class="casals-box-name">casals-backend</div>
                        <div class="casals-box-note">conductor · IC controller of the orchestra</div>
                        <div class="cycles-tag">cycles treasury</div>
                      </div>
                      <div class="casals-box casals-fe">
                        <div class="casals-box-name">casals-frontend</div>
                        <div class="casals-box-note">the team's console</div>
                      </div>
                      <div class="casals-box casals-wasms">
                        <div class="casals-box-name">casals-wasms</div>
                        <div class="casals-box-note">WASM registry · sha256</div>
                      </div>
                      <div class="commanders">
                        <div class="commanders-label">commanders</div>
                        <div class="commanders-dots">
                          <span class="commander c1" title="ops"></span>
                          <span class="commander c2" title="platform"></span>
                          <span class="commander c3" title="realms"></span>
                        </div>
                        <div class="commanders-label">scoped permissions</div>
                      </div>
                    </div>
                    <div class="arrow-down ca-arrow">↓<span class="arrow-label">controls</span></div>

                    <div class="grow-wrap">
                      <div class="axis-y">more users →</div>
                      <div class="orchestra">
                        <div class="orch-label">Orchestra</div>
                        <div class="sections">
                          <div class="section" data-sec="infra">
                            <div class="sec-label">Infra</div>
                            <div class="stand" data-stand="app">
                              <div class="stand-label app-label">your application</div>
                              <div class="cans">
                                <span class="can">backend</span>
                                <span class="can">frontend</span>
                                <span class="can extra">index</span>
                                <span class="can extra">assets</span>
                              </div>
                            </div>
                            <div class="stand" data-stand="shared">
                              <div class="stand-label">shared <span class="shared-note">· one for all users</span></div>
                              <div class="cans">
                                <span class="can">registry</span>
                                <span class="can">installer</span>
                                <span class="can">ledger</span>
                              </div>
                            </div>
                          </div>
                          <div class="section" data-sec="users">
                            <div class="sec-label">Users</div>
                            <div class="stand" data-stand="u1">
                              <div class="stand-label">user1 <span class="approver">· user decides</span></div>
                              <div class="cans">
                                <span class="can baton">baton</span>
                                <span class="can">backend</span>
                                <span class="can">frontend</span>
                                <span class="can more-can">···</span>
                              </div>
                            </div>
                            <div class="stand" data-stand="u2">
                              <div class="stand-label">user2 <span class="approver">· user decides</span></div>
                              <div class="cans">
                                <span class="can baton">baton</span>
                                <span class="can">backend</span>
                                <span class="can">frontend</span>
                                <span class="can more-can">···</span>
                              </div>
                            </div>
                            <div class="stand" data-stand="more">
                              <div class="stand-label">…</div>
                              <div class="cans"><span class="can">···</span></div>
                            </div>
                          </div>
                        </div>
                        <div class="axis-x">more canisters →</div>
                      </div>
                    </div>
                  </div>
                  <aside class="step-panel">{"".join(details)}
                  </aside>
                </div>
              </section>
            </div>"""
        return body, N_STEPS, ",".join(PHASES)


class AuthoritySlide(Slide):
    def render(self) -> tuple[str, int, str]:
        body = f"""
            <div class="page-step active">
              <section class="arch-box authority-slide wide-slide">
                <header class="box-header">
                  <h2>The result: four layers of authority</h2>
                  <p>Who can change what — and who must agree. Numbers refer to the step that introduced each layer.</p>
                </header>
                <div class="gov-diagram">
                  <div class="gov-tier gov-multisig">
                    {badge(2)}
                    <div class="gov-tier-label">Multisig</div>
                    <div class="gov-tier-note">Highest authority · IC controller of Casals and of every baton · rarely used</div>
                  </div>
                  <div class="gov-arrow">↓</div>
                  <div class="gov-tier gov-casals">
                    {badge(6)}
                    <div class="gov-tier-label">casals-backend · casals-frontend</div>
                    <div class="gov-tier-note">Conductor and console · day-to-day create / upgrade / cycles · commanders with scoped permissions</div>
                  </div>
                  <div class="gov-arrow">↓</div>
                  <div class="gov-tier gov-baton">
                    {badge(7)}
                    <div class="gov-tier-label">Baton — one per stand</div>
                    <div class="gov-tier-note">Team proposes and advises · user decides · upgrades the whole stand as one unit</div>
                  </div>
                  <div class="gov-arrow">↓</div>
                  <div class="gov-tier gov-apps">
                    {badge(1)}
                    <div class="gov-tier-label">Stand canisters</div>
                    <div class="gov-tier-note">backend · frontend · … — per user, plus the shared infrastructure</div>
                  </div>
                  <p class="gov-footnote">Casals never embeds voting. It executes approved actions.</p>
                </div>
              </section>
            </div>"""
        return body, 1, ""


EXAMPLES_PATTERNS: list[tuple[str, str]] = [
    ("Multi-tenant SaaS", "One backend and frontend per customer. The customer owns the instance; the vendor ships the updates."),
    ("White-label apps", "The same code under many brands. Each brand decides when it takes the next version."),
    ("Per-DAO deployments", "Treasury, voting, forum — a set of canisters per DAO, operated by a shared team, governed by that DAO."),
    ("Agencies and studios", "One team maintaining canisters for many clients under a single multisig, with scoped access per client."),
]


class ExamplesSlide(Slide):
    """Who Casals is built for: the general shape of the problem."""

    def render(self) -> tuple[str, int, str]:
        patterns = "".join(
            f"""
                  <div class="ex-card ex-pattern">
                    <div class="ex-name">{html.escape(name)}</div>
                    <p class="ex-text">{html.escape(text)}</p>
                  </div>"""
            for name, text in EXAMPLES_PATTERNS
        )
        body = f"""
            <div class="page-step active">
              <section class="arch-box examples-slide wide-slide">
                <header class="box-header">
                  <h2>What Casals is built for</h2>
                  <p>Many instances of the same application, for many owners — and one team that has to keep every one of them upgraded, funded and safe.</p>
                </header>
                <div class="ex-body">
                  <div class="ex-grid ex-grid-2">{patterns}
                  </div>
                </div>
              </section>
            </div>"""
        return body, 1, ""


class TodaySlide(Slide):
    """Closing slide: gos.earth, the deployment Casals conducts today."""

    def render(self) -> tuple[str, int, str]:
        body = f"""
            <div class="page-step active">
              <section class="arch-box examples-slide wide-slide">
                <header class="box-header">
                  <h2>Running today: gos.earth</h2>
                  <p>Many governance operating systems on one shared machine — conducted by Casals.</p>
                </header>
                <div class="today-split">
                  <div class="today-text">
                    <div class="today-block">
                      <div class="today-h">GOS — Governance Operating System</div>
                      <p>The operating system of a society, rewritten as software: who belongs, who decides, what the treasury may spend, which rules execute without asking anyone. One <strong>realm</strong> is one such system, on its own canisters.</p>
                    </div>
                    <div class="today-block">
                      <div class="today-h">GOS-as-a-Service — gos.earth</div>
                      <p>The control plane that hosts many of them side by side. A community deploys a GOS; gos.earth registers it as a realm, provisions the canisters, keeps them funded and current. The community remains sovereign over its own system.</p>
                    </div>
                    <div class="today-block">
                      <div class="today-h">How it uses Casals</div>
                      <ul class="bullets-hollow">
                        <li>The registry accepts a deploy; the <strong>installer</strong> asks the <strong>Casals conductor</strong>.</li>
                        <li>The conductor creates the realm as a new <strong>stand</strong> in Deployments, streams the GOS build from <strong>casals-wasms</strong> and funds it from the treasury.</li>
                        <li>A new GOS release rolls out realm by realm; each realm's <strong>baton</strong> (Casals and the realm itself) must agree before the upgrade lands.</li>
                      </ul>
                    </div>
                  </div>
                  <div class="today-diagram">
                    <div class="multisig-pill">Multisig</div>
                    <div class="arrow-down">↓</div>
                    <div class="casals-row">
                      <div class="casals-box"><div class="casals-box-name">casals-backend</div><div class="casals-box-note">conductor · treasury</div></div>
                      <div class="casals-box casals-wasms"><div class="casals-box-name">casals-wasms</div><div class="casals-box-note">GOS builds</div></div>
                    </div>
                    <div class="arrow-down">↓<span class="arrow-label">controls</span></div>
                    <div class="orchestra">
                      <div class="orch-label">gos.earth orchestra</div>
                      <div class="sections">
                        <div class="section">
                          <div class="sec-label">Infra</div>
                          <div class="stand">
                            <div class="stand-label">installer</div>
                            <div class="cans"><span class="can">realm-installer</span></div>
                          </div>
                          <div class="stand">
                            <div class="stand-label">realm-registry</div>
                            <div class="cans"><span class="can">backend</span><span class="can">frontend</span></div>
                          </div>
                        </div>
                        <div class="section">
                          <div class="sec-label">Deployments</div>
                          <div class="stand">
                            <div class="stand-label">realm <em>alice</em></div>
                            <div class="cans"><span class="can baton">baton</span><span class="can">backend</span><span class="can">frontend</span><span class="can">token</span></div>
                          </div>
                          <div class="stand">
                            <div class="stand-label">realm <em>bob</em></div>
                            <div class="cans"><span class="can baton">baton</span><span class="can">backend</span><span class="can">frontend</span><span class="can">token</span></div>
                          </div>
                          <div class="stand stand-more">
                            <div class="stand-label">…</div>
                            <div class="cans"><span class="can">···</span></div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </section>
            </div>"""
        return body, 1, ""



SPECIAL_MARKERS: dict[str, type[Slide]] = {
    "title": TitleSlide,
    "rationale": RationaleSlide,
    "authority": AuthoritySlide,
    "examples": ExamplesSlide,
    "today": TodaySlide,
}


def parse_steps(md_text: str) -> list[Slide]:
    slides: list[Slide] = []
    blocks = re.split(r"\n(?=# )", md_text.strip())

    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        h1 = lines[0]
        if not h1.startswith("# "):
            continue

        marker = ""
        bullets: list[tuple[int, str]] = []
        bullet_lines: list[str] = []

        for line in lines[1:]:
            if line.startswith("## "):
                if bullet_lines:
                    bullets.extend(parse_bullets(bullet_lines))
                    bullet_lines = []
                marker = line[3:].strip()
            elif line.startswith("- "):
                bullet_lines.append(line)
            elif line.strip() == "" and bullet_lines:
                bullets.extend(parse_bullets(bullet_lines))
                bullet_lines = []

        if bullet_lines:
            bullets.extend(parse_bullets(bullet_lines))

        if h1.startswith("# Section"):
            slides.append(SectionSlide("section", marker))
        elif marker in SPECIAL_MARKERS:
            slides.append(SPECIAL_MARKERS[marker]("special", marker, bullets))
        else:
            slides.append(ContentSlide("content", marker, bullets))

    return slides


def slide_title(slide: Slide) -> str:
    if isinstance(slide, TitleSlide):
        return TITLE_MAIN
    if isinstance(slide, SectionSlide):
        key = slide.marker.replace("section ", "").strip()
        return SECTION_TITLES.get(key, (key,))[0]
    if isinstance(slide, RationaleSlide):
        return "Why Casals"
    if isinstance(slide, AuthoritySlide):
        return "Authority"
    if isinstance(slide, ExamplesSlide):
        return "Built for"
    if isinstance(slide, TodaySlide):
        return "Running today"
    if isinstance(slide, ContentSlide):
        return slide.marker
    return "Slide"


def build_html(slides: list[Slide]) -> str:
    titles = [slide_title(s) for s in slides]
    pages_html = []

    for i, slide in enumerate(slides):
        body, steps, phases = slide.render()
        phase_attr = f' data-phases="{html.escape(phases)}"' if phases else ""
        pages_html.append(f"""
        <div class="page{" active" if i == 0 else ""}" data-slide="{i}" data-steps="{steps}"{phase_attr}>
            {body}
        </div>""")

    title_str = " · ".join(titles)
    pages_joined = "\n".join(pages_html)
    css = CSS.replace("__PHASE_CSS__", _phase_css())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title_str)}</title>
  <style>{css}
  </style>
</head>
<body>
  <article class="deck">
    <div class="viewport" id="viewport">
{pages_joined}
    </div>
    <nav class="dots" id="dots" aria-label="Slide navigation"></nav>
  </article>
  <script>{DECK_JS}
  </script>
</body>
</html>
"""


def main() -> int:
    if not MD_PATH.is_file():
        print(f"Missing {MD_PATH}", file=sys.stderr)
        return 1

    slides = parse_steps(MD_PATH.read_text(encoding="utf-8"))
    if not slides:
        print("No slides parsed from steps.md", file=sys.stderr)
        return 1

    HTML_PATH.write_text(build_html(slides), encoding="utf-8")
    print(f"Wrote {HTML_PATH.name} ({len(slides)} slides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
