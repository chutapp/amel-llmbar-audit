# Pilot plan (1 week, ~$30, GO/NO-GO gate)

## Question

Does AMEL-style batched-context administration change LLMBar's judge
rankings, or are the rankings robust to it?

If rankings shift meaningfully under the pilot, commit to the full
audit (2 months). If shifts are small or qualitative-only, stop and
revisit whether this is a paper.

## Design

### Benchmark
- **LLMBar Natural subset** (100 pairwise items). Smaller than the
  Adversarial subsets; cheaper to pilot; still rank-informative.

### Judge models (3)
- GPT-4.1 Nano (high AMEL effect in main study, d = -0.34)
- Claude Haiku 4.5 (mid AMEL, d = -0.22)
- Llama 3.2 3B (open-weight, d = -0.32)

### Administration regimes (2)
- **Fresh**: each item in its own conversation, no prior history.
- **Batched**: 50-item conversation accumulating prior judgments.

### Repetitions
- 5 reps per (judge, item, regime) at T = 1.0 — enough to compute
  modal preference per cell.

### Total calls
- 100 items × 3 judges × 2 regimes × 5 reps = 3,000 calls
- Cost estimate: ~$15-30 (Nano + Haiku dominate; Llama is free)

## Decision gate

After the pilot, compute three metrics:

1. **Per-judge preference flip rate**: fraction of items where modal
   preference (A vs B) differs between fresh and batched.
2. **Top-1 ranking shift**: does the highest-ranked judge change
   between regimes?
3. **Pairwise judge-ranking correlation**: Spearman ρ between
   per-item-rate-of-A in fresh vs batched, per judge.

### Commit criteria

- Any ONE of:
  - Top-1 ranking changes between regimes
  - Per-judge flip rate ≥ 10% on at least one judge
  - Spearman ρ < 0.85 between fresh and batched per-judge

→ Commit to full audit (LLMBar Adversarial subsets + JudgeBench +
6-8 judges + ~$100-200 + 4-6 weeks).

### Stop criteria

- All judges show < 5% flip rate AND Spearman ρ > 0.95 AND top-1
  unchanged → rankings are robust to AMEL on LLMBar; stop, write a
  short null-result note (not a full paper).

## Concrete file plan for the pilot

- `runners/run_llmbar_pilot.py` — async runner, both regimes, 3
  judges
- `data/llmbar/raw_items.json` — pulled from
  `datasets.load_dataset("princeton-nlp/llmbar")`
- `data/llmbar/responses.jsonl` — per-judge per-rep raw outputs
- `analysis/pilot_rankshift.py` — computes the three gate metrics
- `results/pilot_rankshift.json` — gate decision input

## Open questions before pilot

- Confirm LLMBar Natural subset license permits redistribution
- Confirm which JudgeBench split to target if pilot passes
- Decide whether to include a "fresh + reasoning" vs "batched +
  reasoning" condition for thinking-budget models
