"""Sanity-check the pilot result before any claim is made.

Specifically checks for the most likely false-positive paths:
  1. Parse-failure asymmetry (one regime has many unparseable responses
     that get scored as 'wrong').
  2. Degenerate answer distributions (judge just always answers '1'
     under one regime; accuracy then = ground-truth label balance).
  3. A/B position bias (judge prefers position A regardless of content;
     swap masks would systematically reverse).
  4. Echo / parroting (judge's batched answer just copies the modal
     prior assistant label).
  5. Ground-truth-in-context confound: my batched regime feeds true
     LLMBar labels as the "prior assistant judgments." That is more
     like in-context learning than like AMEL-style batched judging.
     Quantify how much this matters by checking whether judge accuracy
     under batched correlates with the prior-context's correctness
     density.

Output: results/pilot_diagnostics.json + a printed verdict.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
_V2 = REPO / "data" / "llmbar" / "pilot_v2_responses.jsonl"
_V1 = REPO / "data" / "llmbar" / "pilot_responses.jsonl"
RESP = _V2 if _V2.exists() else _V1
ITEMS = REPO / "data" / "llmbar" / "natural_items.json"
OUT = REPO / "results" / ("pilot_v2_diagnostics.json" if RESP == _V2 else "pilot_diagnostics.json")


def main():
    rows = [json.loads(l) for l in open(RESP) if l.strip()]
    items = json.loads(ITEMS.read_text())

    # Ground-truth label distribution in LLMBar Natural
    gt_dist = Counter(it["label"] for it in items)
    print(f"LLMBar Natural ground-truth label distribution: {dict(gt_dist)}")
    base_rate_label_1 = gt_dist[1] / sum(gt_dist.values())
    print(f"Base rate P(label==1) in raw dataset: {base_rate_label_1:.3f}")

    judges = sorted(set(r["judge"] for r in rows))
    regimes = ["fresh", "batched"]
    out = {"per_judge_regime": {}, "verdicts": []}

    for j in judges:
        out["per_judge_regime"][j] = {}
        for reg in regimes:
            sub = [r for r in rows if r["judge"] == j and r["regime"] == reg]
            n = len(sub)
            parsed = [r["parsed_label"] for r in sub if r["parsed_label"] in ("1", "2")]
            unparseable = [r for r in sub if r["parsed_label"] not in ("1", "2")]
            n_parse = len(parsed)
            n_unp = len(unparseable)

            # answer distribution
            c1 = parsed.count("1"); c2 = parsed.count("2")
            p1 = c1 / n_parse if n_parse else None

            # accuracy (against truth-after-swap)
            correct = sum(1 for r in sub
                          if r["parsed_label"] is not None
                          and r["parsed_label"] == r["truth_label_after_swap"])
            acc = correct / n_parse if n_parse else None

            # A/B position bias: for sub-set where swap==True, "answer 1" means output_2;
            # so the rate of "1" in (swap=True) vs (swap=False) cells reveals position bias.
            sw_true = [r for r in sub if r["swap"] is True and r["parsed_label"] in ("1", "2")]
            sw_false = [r for r in sub if r["swap"] is False and r["parsed_label"] in ("1", "2")]
            p1_sw_true = sum(1 for r in sw_true if r["parsed_label"] == "1") / len(sw_true) if sw_true else None
            p1_sw_false = sum(1 for r in sw_false if r["parsed_label"] == "1") / len(sw_false) if sw_false else None

            # sample of raw responses
            sample_raw = [r["raw_response"][:80] for r in sub[:6]]

            out["per_judge_regime"][j][reg] = {
                "n_total": n,
                "n_parseable": n_parse,
                "n_unparseable": n_unp,
                "unparseable_rate": round(n_unp / n, 4) if n else None,
                "answer_dist_p_of_1": round(p1, 4) if p1 is not None else None,
                "accuracy_among_parseable": round(acc, 4) if acc is not None else None,
                "p_of_1_when_swap_true": round(p1_sw_true, 4) if p1_sw_true is not None else None,
                "p_of_1_when_swap_false": round(p1_sw_false, 4) if p1_sw_false is not None else None,
                "position_bias_gap": (
                    round(p1_sw_false - p1_sw_true, 4)
                    if p1_sw_true is not None and p1_sw_false is not None else None
                ),
                "sample_raw_responses": sample_raw,
            }

    # Verdict checks
    for j in judges:
        for reg in regimes:
            d = out["per_judge_regime"][j][reg]
            if d["unparseable_rate"] and d["unparseable_rate"] > 0.10:
                out["verdicts"].append(
                    f"[FLAG] {j} {reg}: unparseable rate {d['unparseable_rate']:.2%} "
                    f"-- may inflate apparent accuracy difference")
            if d["answer_dist_p_of_1"] is not None and (
                d["answer_dist_p_of_1"] < 0.15 or d["answer_dist_p_of_1"] > 0.85
            ):
                out["verdicts"].append(
                    f"[FLAG] {j} {reg}: answer distribution P(1)={d['answer_dist_p_of_1']:.2%} "
                    f"-- judge may be degenerate")
            if d["position_bias_gap"] is not None and abs(d["position_bias_gap"]) > 0.30:
                out["verdicts"].append(
                    f"[FLAG] {j} {reg}: position bias gap {d['position_bias_gap']:+.2%} "
                    f"-- judge may be choosing position not content")

    # CRITICAL: confound check on batched regime
    # My batched regime feeds LLMBar ground-truth labels as the "prior
    # assistant" turns. That is more like in-context learning from gold
    # labels than like AMEL-style batched judging (where the prior would
    # be the model's own answers, not gold).
    out["confound_check"] = {
        "design_note": (
            "Batched-regime conversations feed LLMBar ground-truth labels "
            "as the prior assistant judgments. This is ICL-from-gold-labels, "
            "not AMEL-style batching. Apparent accuracy improvements under "
            "batched (Nano, Llama) may be ICL, not history-bias signal."
        ),
        "implication": (
            "The pilot result as run is informative ('rankings change "
            "between fresh and batched-with-correct-prior') but does NOT "
            "isolate AMEL. Full audit must redo batched with one of: "
            "(a) polarity-skewed labels in prior (matches AMEL no_sat/yes_sat), "
            "(b) model's own actual prior judgments (matches production batching), "
            "or (c) balanced random labels (matches AMEL neutral). "
            "Recommend (b) as the most production-realistic."
        ),
    }

    OUT.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUT}")
    print()
    for v in out["verdicts"]:
        print(v)
    if not out["verdicts"]:
        print("[no parse / answer-dist / position-bias flags]")
    print()
    print("===== CONFOUND CHECK =====")
    print(out["confound_check"]["design_note"])
    print()
    print(out["confound_check"]["implication"])


if __name__ == "__main__":
    main()
