# AMEL audit of LLM-judge benchmarks

Companion paper to **AMEL** (Temkit, 2026; `chutapp/amel`,
arXiv:2605.22714). AMEL documented a small but consistent
conversation-history bias in LLM judges. This repo asks the
operational follow-on question: **does that bias change which model
"wins" on the most-used LLM-judge benchmarks?**

Concretely, this work re-runs LLMBar (Zeng et al. 2023), JudgeBench,
and adjacent benchmarks under two administration regimes ---
fresh-context-per-item and AMEL-style batched-context --- and
reports the rank reshuffle.

## Status

Pre-pilot. Pilot phase (LLMBar Natural subset, 3 models) will decide
whether to commit to the full audit.

## Layout

```
.
├── src/                  ← framework vendored from chutapp/amel (see src/VENDORED_FROM.md)
├── paper/                ← LaTeX manuscript
├── runners/              ← per-benchmark experiment runners
├── analysis/             ← rank-shift, agreement, contamination analyses
├── data/                 ← benchmark items + responses (released after pilot)
├── results/              ← JSON statistics
└── plans/                ← internal planning notes
```

## Pilot plan

See `plans/pilot.md` for the 1-week pilot design and decision gate.

## Citation

If you use this work, please cite both papers:

- AMEL (Temkit 2026, arXiv:2605.22714) --- the underlying
  conversation-history bias finding
- This work (audit) --- once published

## License

Code: MIT (`LICENSE`). Data: CC-BY-4.0 (added on first data release).
