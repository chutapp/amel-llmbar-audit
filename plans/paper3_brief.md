# Research brief: paper 3 (the "cure")

Status: open research question. This brief defines what needs to be
found. It does not propose a solution. The team that picks up this
project should design and run the work themselves.

## Where this fits

Two earlier papers in the program:

- **Paper 1 -- AMEL** (Temkit, 2026; arXiv:2605.22714).
  Documented that LLM judges shift their verdicts when prior
  conversation history accumulates polarity-skewed prior judgments.
  Effect is small but consistent (overall $d \approx -0.17$).

- **Paper 2 -- LLMBar audit** (in progress, see
  `chutapp/amel-llmbar-audit`).
  Documented that aggregate LLM-judge benchmark scores can be
  administration-regime-stable while *per-item* verdicts are
  regime-dependent and only cancel by coincidence. Two of five
  tested production judges (Haiku, Sonnet) lose 15--33 accuracy
  points under batched administration; the other three preserve
  accuracy but flip ~50 % of per-item verdicts.

The audit paper says: the problem is real and measurable. It does
not say: what to do about it, or whether humans show the same
pattern.

Paper 3 picks one of those two open questions and answers it.

## The two candidate directions

Either direction is a publishable paper if the answer is honest.
The team should pick the one that fits their interests and
resources, run it cleanly, and write it up. Picking both at once
is a worse paper, not a better one.

### Direction A -- the cure (mitigation method)

**Open question.** Is there an administration-time intervention
that restores per-item verdict consistency across regimes, without
requiring full fresh-context replay?

**Why it matters.** Production LLM-judge pipelines often cannot
afford full fresh-context (token cost, prompt-caching loss). If a
cheap mitigation exists, deployers can keep using batched
administration safely. If no cheap mitigation exists, the
recommendation is unambiguous: always fresh-context, and the
operational cost of that recommendation needs to be quantified.

**What the team needs to find.** A mitigation that, applied to
batched administration, drives the fresh-vs-batched per-item
agreement rate to within a small margin of the fresh-vs-fresh
noise floor (the noise floor itself is measured by paper 2 phase
1). "Small margin" is for the team to define in their
prereg -- 5--10 percentage points is a defensible bar.

**What candidates are worth trying.** Up to the team. Reasonable
starting set:
  - per-item explicit instruction to disregard prior turns
  - periodic context reset (every K items)
  - selective re-ask in fresh context on uncertain items
  - logit-bias correction at decode time
  - few-shot demonstrations of regime-invariant judging
  - ensemble across regimes

Whether any of these work is exactly the question. Some may
trivially work, some may not work at all. Both outcomes are
publishable.

**What success looks like.**
  - At least one mitigation that demonstrably and reproducibly
    restores per-item agreement, with the result holding across
    multiple judges and at least one cross-benchmark setting.
  - A failure mode characterised: which mitigations work for which
    judges, and which do not.
  - An honest discussion of the deployment cost (extra tokens,
    extra latency, extra calls) of each mitigation that works.

**What success does not look like.**
  - A single mitigation that works only on one judge on one
    benchmark.
  - A mitigation whose deployment cost exceeds the fresh-context
    baseline (then just use fresh-context).
  - A mitigation that works only when the deployer already knows
    the gold labels.

### Direction B -- the human comparator

**Open question.** Do human raters exhibit the same per-item
cancellation phenomenon when administered the LLMBar items under
a fresh vs. batched protocol, or is the phenomenon LLM-specific?

**Why it matters.** Two very different stories follow from the
answer:
  - If humans also drift between regimes with offsetting flips,
    LLM judges are *measuring something real about the items*
    just in an unreliable way, and the methodological prescription
    is administration-standardisation for any rater (human or LLM).
  - If humans are stable across regimes and LLMs are not, LLM
    judges have a defect humans do not, and the framing becomes
    "LLM judges introduce a failure mode that does not exist in
    the human protocol they are meant to replace."

The second story is much stronger as a critique of LLM-as-judge
methodology. The first story is more interesting as a methodology
finding about judging in general. Either is a paper.

**What the team needs to find.** Rigorous per-item rater data on
LLMBar Natural (and ideally one Adversarial subset) under both
administration regimes, with enough power to compare per-item
agreement rates between humans and the five LLM judges already
measured in paper 2.

**Sample-size sketch (not prescription).** ~100 items × 4--6
human raters × fresh + batched × 2 reps = ~5,000 human rater
judgments. At platform-typical compensation this is ~$300--600
plus several weeks of recruitment and coordination. The team
should sample-size up if they want anything more confident than
directional.

**Operational caveats the team should plan for.**
  - Humans do not naturally "judge 50 items in one conversation."
    The batched-regime protocol for humans needs careful design:
    one session of 50 sequential judgments with no breaks is the
    most faithful analogue, but rater fatigue confounds the
    comparison with LLMs. Pre-register the protocol.
  - Inter-rater agreement is itself a confound: paper 1's IRR
    study found Krippendorff's $\alpha$ ranging $0.28$ (code
    review) to $0.69$ (meals) on category labels. LLMBar
    pairwise preference may have its own inter-rater issues; this
    paper needs to measure them or cite an existing measurement.
  - Recruitment via an online freelance platform is the obvious
    channel; the AMEL paper used one successfully. Domain
    expertise per item type is the team's call.

**What success looks like.**
  - Defensible per-item agreement numbers for humans under both
    regimes, with confidence intervals.
  - A clean comparison plot: human fresh-vs-batched agreement vs.
    LLM fresh-vs-batched agreement, per item.
  - An honest discussion of the protocol-asymmetry caveat (humans
    and LLMs are not doing exactly the same thing under "batched";
    the team must state explicitly what the comparison does and
    does not establish).

**What success does not look like.**
  - A claim that humans are "better" or "worse" than LLMs in
    general. The claim is about regime-stability only.
  - A study that uses fewer than ~4 human raters, where
    per-item modal agreement is too noisy to interpret.

## Recommendation on direction

The folder name (`amel-llmbar-cure`) implies the mitigation
direction is the working assumption. The team is free to pick
the human comparator instead if that is what they want to do.

If the team wants a steer: direction A is cheaper, faster, more
self-contained, and produces an immediately actionable artifact
(a mitigation recipe). Direction B is more conceptually
interesting, harder to run cleanly, and produces a claim about
the relative reliability of human vs. LLM raters that has broader
methodological reach.

Either way, do not try to do both in one paper. Pick one. Run it.
Write it up.

## What the team inherits

- Paper 1 (AMEL) data and code: `chutapp/amel`. Item pool, parser,
  conversation builder, runners for OpenAI, Anthropic, Google,
  DeepSeek, and Ollama models.
- Paper 2 (LLMBar audit) data and code: `chutapp/amel-llmbar-audit`.
  LLMBar Natural item dump, pilot responses for 5 judges across
  fresh and batched regimes, sequential-batching runner,
  rank-shift analysis.
- Both vendored `src/` from `chutapp/amel` at the relevant commit
  SHA (see each repo's `src/VENDORED_FROM.md`).

The team should vendor `src/` from `chutapp/amel-llmbar-audit`
into this repo (or a fresh checkout of `chutapp/amel`) at the
commit they pin to. They should not re-do paper 2's pilot or
phase-1 work; those numbers are inputs, not deliverables.

## What is out of scope for paper 3

- Mechanistic interpretability of AMEL inside the model. That is
  a separate (fourth) paper, requires different tools (SAEs,
  activation patching), and should not be folded in here.
- Extending the LLMBar audit to more benchmarks or more judges.
  That is a revision-of-paper-2 question, not a paper-3 question.
- Re-measuring AMEL on different domains. Out of scope.

## What this brief deliberately does not say

- Which mitigation method to try first. Direction A only works as
  a paper if the team genuinely doesn't know in advance which
  candidate will win; that is the experiment.
- Whether humans actually drift less than LLMs. Direction B only
  works as a paper if the team treats the answer as genuinely
  open; predicting the outcome ahead of running it is not honest
  research.
- The exact statistical method for the human-vs-LLM comparison.
  The team should pick what fits the data once collected.

The brief intentionally stops here. The next team takes it from
the open question.
