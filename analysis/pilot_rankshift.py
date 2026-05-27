"""Compute the pilot decision-gate metrics from pilot_responses.jsonl.

Three metrics, per judge:
  1. Per-judge preference flip rate (modal fresh vs modal batched).
  2. Top-1 ranking shift (does the highest-accuracy judge change between
     regimes?).
  3. Per-judge Spearman rho between per-item rate-of-"1" under fresh vs
     batched.

Commit criteria (any one is sufficient):
  - top-1 changes between regimes
  - any judge flip rate >= 10%
  - any judge Spearman rho < 0.85

Output: results/pilot_rankshift.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

REPO = Path(__file__).resolve().parent.parent
# Default to v2 responses (sequential-batch design). Fall back to v1 if
# v2 is missing; v1 is the flawed ICL-from-gold-labels design retained
# only as a reproducibility audit trail.
_V2 = REPO / "data" / "llmbar" / "pilot_v2_responses.jsonl"
_V1 = REPO / "data" / "llmbar" / "pilot_responses.jsonl"
RESPONSES = _V2 if _V2.exists() else _V1
OUT = REPO / "results" / ("pilot_v2_rankshift.json" if RESPONSES == _V2 else "pilot_rankshift.json")
OUT.parent.mkdir(parents=True, exist_ok=True)


def modal(labels: list[str]) -> str | None:
    """Modal label, returning None on tie or empty."""
    labels = [l for l in labels if l in ("1", "2")]
    if not labels:
        return None
    c1 = labels.count("1")
    c2 = labels.count("2")
    if c1 == c2:
        return None
    return "1" if c1 > c2 else "2"


def main():
    rows = [json.loads(l) for l in open(RESPONSES) if l.strip()]
    print(f"Loaded {len(rows)} rows")

    # Group by (judge, regime, item) -> [labels across reps]
    cells: dict[tuple, list[str]] = defaultdict(list)
    truths: dict[tuple, str] = {}  # (judge, regime, item) -> truth (per-swap)
    for r in rows:
        key = (r["judge"], r["regime"], r["item_id"])
        cells[key].append(r["parsed_label"])
        # truth_label is per-row (depends on swap), but for accuracy we need
        # to align: a row's parsed_label is correct iff parsed_label == truth_label_after_swap
        # so we don't keep a single truth per cell — accuracy is computed row-wise below.

    judges = sorted(set(r["judge"] for r in rows))
    regimes = ["fresh", "batched"]

    # Per-judge accuracy under each regime (row-wise: parsed == truth-after-swap)
    accuracy: dict[str, dict[str, float]] = {j: {} for j in judges}
    for j in judges:
        for reg in regimes:
            sub = [r for r in rows if r["judge"] == j and r["regime"] == reg]
            correct = sum(1 for r in sub if r["parsed_label"] is not None
                          and r["parsed_label"] == r["truth_label_after_swap"])
            parseable = sum(1 for r in sub if r["parsed_label"] is not None)
            accuracy[j][reg] = {
                "n": len(sub),
                "n_parseable": parseable,
                "n_correct": correct,
                "accuracy": round(correct / parseable, 4) if parseable else None,
            }

    # Per-judge MODAL preference per (regime, item): does the modal answer change?
    # Compare modal-fresh vs modal-batched on the same item.
    flip_stats: dict[str, dict] = {}
    spearman: dict[str, dict] = {}
    for j in judges:
        items_fresh = {k[2]: modal(v) for k, v in cells.items() if k[0] == j and k[1] == "fresh"}
        items_batched = {k[2]: modal(v) for k, v in cells.items() if k[0] == j and k[1] == "batched"}
        common = sorted(set(items_fresh) & set(items_batched))
        flips = 0
        ties = 0
        comparable = 0
        for it in common:
            mf = items_fresh[it]
            mb = items_batched[it]
            if mf is None or mb is None:
                ties += 1
                continue
            comparable += 1
            if mf != mb:
                flips += 1
        flip_stats[j] = {
            "n_items": len(common),
            "n_comparable": comparable,
            "n_ties": ties,
            "n_flips": flips,
            "flip_rate": round(flips / comparable, 4) if comparable else None,
        }

        # Spearman rho on per-item rate-of-"1" (continuous, not modal)
        def rate_of_one(labels: list[str]) -> float | None:
            valid = [l for l in labels if l in ("1", "2")]
            if not valid:
                return None
            return sum(1 for l in valid if l == "1") / len(valid)
        rates_f = [rate_of_one([l for l in cells[(j, "fresh", it)]]) for it in common]
        rates_b = [rate_of_one([l for l in cells[(j, "batched", it)]]) for it in common]
        paired = [(a, b) for a, b in zip(rates_f, rates_b) if a is not None and b is not None]
        if len(paired) >= 5:
            af, ab = zip(*paired)
            rho, p = stats.spearmanr(af, ab)
            spearman[j] = {"n_pairs": len(paired), "rho": round(float(rho), 4), "p": float(p)}
        else:
            spearman[j] = {"n_pairs": len(paired), "rho": None, "p": None}

    # Top-1 by accuracy under each regime
    top1 = {}
    for reg in regimes:
        ranked = sorted(
            ((j, accuracy[j][reg]["accuracy"]) for j in judges
             if accuracy[j][reg]["accuracy"] is not None),
            key=lambda kv: kv[1], reverse=True,
        )
        top1[reg] = ranked[0][0] if ranked else None

    top1_changed = top1.get("fresh") != top1.get("batched")

    # Decision gate
    any_flip_above = any(
        (fs.get("flip_rate") or 0.0) >= 0.10 for fs in flip_stats.values()
    )
    any_rho_below = any(
        (sp.get("rho") is not None and sp["rho"] < 0.85) for sp in spearman.values()
    )
    commit = top1_changed or any_flip_above or any_rho_below

    out = {
        "n_rows": len(rows),
        "judges": judges,
        "regimes": regimes,
        "accuracy": accuracy,
        "modal_flip": flip_stats,
        "per_judge_spearman_fresh_vs_batched": spearman,
        "top1_by_regime": top1,
        "decision_gate": {
            "top1_changed": bool(top1_changed),
            "any_judge_flip_rate_ge_10pct": bool(any_flip_above),
            "any_judge_spearman_rho_lt_0.85": bool(any_rho_below),
            "commit_to_full_audit": bool(commit),
        },
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"Wrote {OUT}")
    print()
    print(f"Top-1 fresh: {top1.get('fresh')}")
    print(f"Top-1 batched: {top1.get('batched')}")
    print(f"  changed: {top1_changed}")
    print()
    print("Per-judge:")
    for j in judges:
        a = accuracy[j]
        f = flip_stats[j]
        s = spearman[j]
        print(f"  {j}")
        print(f"    accuracy   fresh={a['fresh']['accuracy']}  batched={a['batched']['accuracy']}")
        print(f"    flip rate  {f['flip_rate']}  (flips {f['n_flips']}/{f['n_comparable']})")
        print(f"    spearman   rho={s['rho']}  p={s['p']}")
    print()
    print(f"Commit to full audit: {commit}")


if __name__ == "__main__":
    main()
