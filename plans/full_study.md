# Full study plan: judge-benchmark aggregate scores hide regime-dependent per-item unreliability

Working plan after the v2 pilot returned a result narrower but
sharper than the original "LLMBar rankings reshuffle" hypothesis.
This document is the source of truth for what gets run, in what
order, with what budget, and what kills the study at each gate.

## 1. Re-framed contribution

**Original framing (pilot hypothesis):**
> "AMEL-style batched-context administration reshuffles LLMBar's
> judge leaderboard."

The pilot shows this is **not** true. Top-1 is preserved across
regimes (GPT-5.5 wins both at ~0.95).

**Actual framing for the paper (professor's reframe, accepted):**
> "Judge-benchmark aggregate scores can be stable across
> administration regimes while per-item verdicts are
> regime-dependent and only cancel by coincidence. The benchmark
> therefore measures something that depends substantially on a
> methodological choice it does not standardise."

LLMBar is the case study. RewardBench is the cross-benchmark
replication. The per-item-cancellation finding is the conceptual
contribution.

This is a methodology critique, not a benchmark audit. Higher
ceiling, same evidence base.

## 2. Pilot findings (already in hand)

5 judges × 100 LLMBar Natural items × fresh vs batched (N=15) × 3 reps.

| Judge | Fresh acc | Batched acc | Δ | Modal-flip rate (f vs b) |
|---|---|---|---|---|
| Haiku 4.5 | 0.88 | 0.55 | −33pp | 50% |
| Sonnet 4.6 | 0.92 | 0.77 | −15pp | 46% |
| Opus 4.6 | 0.94 | 0.94 | 0 | 52% |
| Nano | 0.87 | 0.88 | 0 | 45% |
| GPT-5.5 | 0.95 | 0.96 | +1pp | 46% |

Top-1 unchanged (GPT-5.5). All judges show ~50% per-item modal
flips. Headline candidate: the three accuracy-stable judges are
stable by cancellation, not by reliability.

## 3. Phase plan

Five phases, each with a decision gate. Phases 0--2 happen before
any commitment to the full study; phases 3--5 are the full study
proper.

### Phase 0 -- free re-analyses on existing pilot data (this week, no API)

Three free analyses on `pilot_v2_responses.jsonl`. Each closes one
reviewer-attackable hole.

| # | Analysis | Output | Gate |
|---|---|---|---|
| 0.1 | Cancellation decomposition: for each accuracy-stable judge, count fresh→batched flips as (correct→incorrect) vs (incorrect→correct). | `results/cancellation_decomposition.json` | Counts roughly balanced → cancellation proven, headline holds. Imbalanced → "stable judges are robustly stable on a subset of items"; headline weakens, reframe needed. |
| 0.2 | Haiku position-vs-polarity check: cross-tabulate Haiku's batched "2" answers against the `swap` flag and against the prior-turn label density. Disambiguates AMEL polarity attractor from raw position bias. | `results/haiku_position_check.json` | Polarity → AMEL-consistent. Position → "≥2 distinct failure modes under batched"; both still publishable, framing changes. |
| 0.3 | Per-item agreement-rate metric replacing Spearman-on-rate-of-1: fraction of (judge, item) pairs where the parsed_label is identical across reps and regimes. | patch to `analysis/pilot_rankshift.py` | Cosmetic but reviewer-correct. |

**Phase 0 gate:** if 0.1 disproves cancellation, the headline weakens and the study is reframed before any more API spend. If 0.2 shows Haiku is position-driven, the paper acknowledges two distinct failure modes (still publishable, just sharper).

### Phase 1 -- fresh-fresh noise floor (this week, ~$50, ~3h)

The single biggest reviewer attack on the pilot: a 50% modal-flip
rate between fresh and batched is only interpretable against the
fresh-vs-fresh noise floor.

| Item | Detail |
|---|---|
| Design | 5 judges × 100 LLMBar Natural items × 3 *additional* fresh reps (independent of the existing 3 fresh reps in the pilot). |
| Comparison | per-(judge, item) modal answer over reps 0--2 vs modal over reps 3--5. Modal-flip rate is the noise floor. |
| Files | `runners/run_fresh_noise_floor.py`, `data/llmbar/noise_floor_responses.jsonl`, `results/noise_floor.json` |
| Cost | ~$50 (Opus dominates). |

**Phase 1 gate:**
- Fresh-fresh modal-flip ≤ 20% → +30pp gap to fresh-vs-batched is real signal → commit to full study.
- Fresh-fresh modal-flip 20--30% → real signal but smaller than headline; commit with revised wording.
- Fresh-fresh modal-flip ≥ 30% → judge noise itself dominates; pause and redesign before any further spend.

### Phase 2 -- length curve (this week, ~$15, ~1h)

AMEL says the effect saturates by N=5. Pilot uses N=15. Need to
verify the regime-dependence bites at short contexts too (otherwise
a reviewer says "this is long-context fatigue, not AMEL").

| Item | Detail |
|---|---|
| Design | 2 judges (Haiku, Nano) × 100 items × N ∈ {1, 3, 5} × 3 reps. N=15 already in pilot. |
| Files | `runners/run_length_curve.py`, `results/length_curve.json` |
| Cost | ~$15. |

**Phase 2 gate:** effect present at N=5 and at-or-near-saturation by N=15 → AMEL-consistent. Effect only appears at N=15 → fatigue not AMEL; reframe accordingly.

### Phase 3 -- Adversarial subsets (main extension, ~$300--500, ~6h)

LLMBar's four Adversarial subsets are where positional / verbosity
manipulations are designed in. Important to know whether AMEL
interacts with those designed-in biases or is orthogonal.

| Item | Detail |
|---|---|
| Design | 5 judges × 4 Adversarial subsets (Neighbor, GPTInst, GPTOut, Manual) × fresh + batched × 3 reps. N=15 for batched. |
| Estimated items | ~300--400 per subset, ~1300 total across 4 subsets. |
| Files | `runners/run_adversarial_full.py`, `data/llmbar/adversarial_*_responses.jsonl`, `results/adversarial_per_subset.json` |
| Cost | ~$300--500 (Opus dominates; if cost is tight, drop Opus and use Sonnet-only as the frontier-Anthropic judge). |

### Phase 4 -- cross-benchmark replication (RewardBench, ~$100, ~3h)

Professor's point: JudgeBench is also pairwise-preference; not a
genuine task-shape replication. RewardBench is single-item
scoring -- a real task-shape difference. One genuine replication
beats five same-shape ones.

| Item | Detail |
|---|---|
| Design | 5 judges × ~500 RewardBench items × fresh + batched (N=15) × 3 reps. |
| Files | `runners/run_rewardbench.py`, `data/rewardbench/`, `results/rewardbench.json` |
| Cost | ~$100. |

### Phase 5 -- paper writing (~1--2 weeks)

Outline:
- §1 Introduction: judge benchmarks have proliferated; methodological assumptions inherited from human-rater protocols don't transfer cleanly.
- §2 Background: AMEL (cited), LLMBar, RewardBench, prior judge-bias audits.
- §3 Methodology: fresh vs batched administration; sequential batching with model's own answers; design rationale; what we deliberately did *not* do (the v1 ICL-from-gold-labels design is explicitly demoted in an Appendix as a methodological cautionary tale).
- §4 Results -- LLMBar Natural: pilot table.
- §5 The cancellation mechanism: phase 0.1 decomposition. Headline figure.
- §6 Robustness: noise floor (phase 1), length curve (phase 2), Adversarial subsets (phase 3).
- §7 Cross-benchmark: RewardBench (phase 4).
- §8 Discussion: implications for how to read judge-benchmark scores.
- §9 Limitations: judge panel breadth, single-subject (judges from 2 providers), no human comparator, no multi-rep variance decomposition.
- §10 Practical recommendations: report fresh-vs-batched delta as part of any judge-benchmark score; standardise administration in the benchmark spec.

## 4. Budget summary

| Phase | API cost (USD) | Wall-clock | Cumulative |
|---|---|---|---|
| 0 | 0 | 2h analyst time | 0 |
| 1 | ~50 | 3h | ~50 |
| 2 | ~15 | 1h | ~65 |
| 3 | ~300--500 | 6h | ~365--565 |
| 4 | ~100 | 3h | ~465--665 |
| **Total** | **~$500--700** | **~15h API + ~2 weeks analyst time** | |

Opus 4.6 dominates the bill. If cost is tight, drop Opus from
phases 3 and 4 (Sonnet stays as the frontier-Anthropic judge);
total drops to ~$250--350.

## 5. Timeline

- Week 1: phases 0, 1, 2. Sharpened pilot writeup. Decision on
  reframing.
- Week 2: phase 3 (Adversarial). First-draft results section.
- Week 3: phase 4 (RewardBench). Cross-benchmark section.
- Weeks 4--5: paper draft. Internal review by professor.
- Week 6: revisions and arXiv submission.

Six weeks from now to arXiv preprint is realistic if there are no
surprises.

## 6. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cancellation hypothesis fails (phase 0.1) | medium | high (headline weakens) | Reframe to "regime-dependent score loss on some judges + per-item-flip on all"; still publishable. |
| Haiku collapse is pure position bias (phase 0.2) | medium | medium (one judge's interpretation changes) | Reframe explicitly: "batched administration triggers ≥2 distinct failure modes." |
| Noise floor is high (≥30%, phase 1) | low--medium | high (kills the headline) | Pause; consider whether the headline is "judges are noisy" instead of "regimes differ." |
| Effect only appears at N=15 (phase 2) | low | medium | Reframe as long-context-fatigue + AMEL; cite both literatures. |
| Adversarial subsets show no regime-dependence (phase 3) | low | low | Still informative: "the effect is orthogonal to the adversarial manipulations." |
| RewardBench shows no regime-dependence (phase 4) | medium | medium | Reframe to "LLMBar-shape-specific"; narrower paper but still real. |
| API model alias drift mid-study | low | medium | Capture deployed fingerprints at every phase boundary (as AMEL paper does). |

## 7. Out of scope for this paper

- Mechanistic interpretability (the AMEL circuit). Separate paper.
- Human-baseline comparison (do humans also drift between regimes on LLMBar?). Separate paper; would require Upwork annotators, ~$300, real coordination.
- Expanding the judge panel beyond 5 (e.g., Gemini, open-weight). Marginal addition; could be added in revision if reviewers ask.
- Mitigation methods (instructing the judge to "ignore prior turns"; resetting context every N items). Operational follow-on; not needed to establish the phenomenon.

## 8. Concrete next actions (in order)

1. Run phase 0.1 (cancellation decomposition) on existing data.
2. Run phase 0.2 (Haiku position-vs-polarity).
3. Patch phase 0.3 metric.
4. Re-share updated pilot writeup with professor; confirm reframing.
5. If go: run phase 1 (noise floor), then phase 2 (length curve).
6. If still go: launch phase 3 (Adversarial), then phase 4 (RewardBench).
7. Draft paper concurrently with phases 3--4.
