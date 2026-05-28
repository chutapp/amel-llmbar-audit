"""Extend v2 pilot to production judges (GPT-5.5, Sonnet 4.6, Opus 4.6).

Why: the v2 pilot ran Nano, Haiku, and Llama. Llama is not a typical
LLMBar judge (3B open-weight, nobody scores LLMBar with it), so it
was the wrong pick for a benchmark-fragility claim. Reviewer would
say "of course a 3B model is unreliable." Replacing Llama's slot
with three production-grade judges that practitioners actually use
to score LLMBar.

Same design as v2 (sequential batching with model's own answers,
strict prompt, N=15, 3 reps, 100 LLMBar Natural items), with two
fixes:
  - Incremental flush for BOTH fresh and batched rows (v2 only
    flushed batched; fresh sat in memory until end-of-judge, with
    obvious data-loss risk).
  - GPT-5 family uses max_completion_tokens, not max_tokens.

Appends to data/llmbar/pilot_v2_responses.jsonl so analysis covers
all judges from a single file. Skips any (judge, regime, item, rep)
already present in the file.

Cost estimate (rough): GPT-5.5 ~$25, Sonnet 4.6 ~$25, Opus 4.6 ~$115.
Total ~$165. Opus dominates the bill.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

from runners.run_llmbar_pilot_v2 import (  # reuse the v2 pieces
    load_llmbar_natural, swap_pair, format_pairwise, parse_pairwise,
    SYSTEM_PROMPT, TEMPERATURE, MAX_TOKENS, REPS, CONTEXT_N,
)

OUT_FILE = REPO / "data" / "llmbar" / "pilot_v2_responses.jsonl"

# New judges to add. (model_name_in_api, provider_kind)
EXTRA_JUDGES = [
    ("gpt-5.5", "openai-gpt5"),
    ("claude-sonnet-4-6", "anthropic"),
    ("claude-opus-4-6", "anthropic"),
]


# ---------- callers ------------------------------------------------

async def call_openai_gpt5(client, msgs, semaphore, model_name):
    """GPT-5 family requires max_completion_tokens (not max_tokens) AND
    burns tokens on internal reasoning before any visible output. Set
    reasoning_effort to minimal and leave headroom (32 tokens) so the
    model's "1" or "2" reply actually emerges."""
    async with semaphore:
        t0 = time.perf_counter()
        try:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json={
                    "model": model_name,
                    "messages": msgs,
                    "temperature": TEMPERATURE,
                    "max_completion_tokens": 32,
                    "reasoning_effort": "none",
                },
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                timeout=120.0,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"], (time.perf_counter() - t0) * 1000
        except Exception as e:
            return f"ERROR: {e}", (time.perf_counter() - t0) * 1000


async def call_anthropic(client, msgs, semaphore, model_name):
    sys_content = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else SYSTEM_PROMPT
    body_msgs = [m for m in msgs if m["role"] != "system"]
    async with semaphore:
        t0 = time.perf_counter()
        try:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": model_name,
                    "system": sys_content,
                    "messages": body_msgs,
                    "max_tokens": MAX_TOKENS,
                    "temperature": TEMPERATURE,
                },
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )
            r.raise_for_status()
            blocks = r.json().get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text, (time.perf_counter() - t0) * 1000
        except Exception as e:
            return f"ERROR: {e}", (time.perf_counter() - t0) * 1000


CALLERS = {
    "openai-gpt5": call_openai_gpt5,
    "anthropic": call_anthropic,
}
CONCURRENCY = {"openai-gpt5": 10, "anthropic": 5}


# ---------- sequential batch --------------------------------------

async def sequential_batch(client, caller, sem, model_name, target_item,
                           target_swap, target_rep, ctx_items):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    ctx_log = []
    for ctx_item, ctx_swap in ctx_items:
        a, b, _ = swap_pair(ctx_item, ctx_swap)
        msgs.append({"role": "user", "content": format_pairwise(ctx_item["input"], a, b)})
        raw, _ = await caller(client, msgs, sem, model_name)
        parsed = parse_pairwise(raw)
        assistant = parsed if parsed in ("1", "2") else (raw or "?")[:8]
        msgs.append({"role": "assistant", "content": assistant})
        ctx_log.append({"item_id": ctx_item["id"], "swap": ctx_swap,
                        "parsed": parsed, "raw_first8": (raw or "")[:8]})

    a, b, truth = swap_pair(target_item, target_swap)
    msgs.append({"role": "user", "content": format_pairwise(target_item["input"], a, b)})
    raw, elapsed = await caller(client, msgs, sem, model_name)
    parsed = parse_pairwise(raw)
    return {
        "judge": model_name,
        "regime": "batched",
        "context_n": len(ctx_items),
        "item_id": target_item["id"],
        "repetition": target_rep,
        "swap": target_swap,
        "truth_label_after_swap": truth,
        "raw_response": raw[:200] if isinstance(raw, str) else "",
        "parsed_label": parsed,
        "response_time_ms": round(elapsed, 2),
        "context_log": ctx_log,
    }


# ---------- per-judge --------------------------------------------

async def run_for_judge(client, model_name, provider_kind, items, existing):
    caller = CALLERS[provider_kind]
    sem = asyncio.Semaphore(CONCURRENCY[provider_kind])

    def flush(row):
        with open(OUT_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")

    # FRESH (with incremental flush this time)
    fresh_specs = []
    for item in items:
        for rep in range(REPS):
            if (model_name, "fresh", item["id"], rep) in existing:
                continue
            seed = abs(hash(f"{model_name}|{item['id']}|fresh_v2|{rep}")) & 0xFFFFFFFF
            swap = (seed & 1) == 1
            a, b, truth = swap_pair(item, swap)
            msgs = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": format_pairwise(item["input"], a, b)},
            ]
            fresh_specs.append((item, rep, swap, truth, msgs))
    print(f"  [{model_name}] fresh: {len(fresh_specs)} calls")

    async def fresh_one(item, rep, swap, truth, msgs):
        raw, elapsed = await caller(client, msgs, sem, model_name)
        return {
            "judge": model_name,
            "regime": "fresh",
            "context_n": 0,
            "item_id": item["id"],
            "repetition": rep,
            "swap": swap,
            "truth_label_after_swap": truth,
            "raw_response": raw[:200] if isinstance(raw, str) else "",
            "parsed_label": parse_pairwise(raw),
            "response_time_ms": round(elapsed, 2),
        }

    done = 0
    for coro in asyncio.as_completed([fresh_one(*s) for s in fresh_specs]):
        row = await coro
        flush(row)
        done += 1
        if done % 50 == 0:
            print(f"    fresh {done}/{len(fresh_specs)}")

    # BATCHED
    batched_specs = []
    for item in items:
        for rep in range(REPS):
            if (model_name, "batched", item["id"], rep) in existing:
                continue
            seed = abs(hash(f"{model_name}|{item['id']}|batched_v2|{rep}")) & 0xFFFFFFFF
            rng = random.Random(seed)
            others = [it for it in items if it["id"] != item["id"]]
            ctx_items = rng.sample(others, k=CONTEXT_N)
            ctx_swaps = [rng.random() < 0.5 for _ in ctx_items]
            target_swap = rng.random() < 0.5
            batched_specs.append((item, rep, target_swap,
                                  list(zip(ctx_items, ctx_swaps))))
    print(f"  [{model_name}] batched: {len(batched_specs)} batches "
          f"(each = {CONTEXT_N + 1} sequential calls)")

    async def batched_one(target_item, target_rep, target_swap, ctx_items):
        return await sequential_batch(client, caller, sem, model_name,
                                      target_item, target_swap, target_rep, ctx_items)

    done = 0
    for coro in asyncio.as_completed([batched_one(*s) for s in batched_specs]):
        row = await coro
        flush(row)
        done += 1
        if done % 25 == 0:
            print(f"    batched {done}/{len(batched_specs)}")


# ---------- main --------------------------------------------------

async def main():
    items = load_llmbar_natural()
    print(f"Items loaded: {len(items)}")

    existing = set()
    if OUT_FILE.exists():
        with open(OUT_FILE) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    existing.add((r["judge"], r["regime"], r["item_id"], r["repetition"]))
        print(f"Existing rows in file: {len(existing)}")

    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY not set; GPT-5.5 will be skipped.")
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY not set; Sonnet + Opus will be skipped.")

    async with httpx.AsyncClient() as client:
        for model_name, kind in EXTRA_JUDGES:
            if kind == "openai-gpt5" and not OPENAI_API_KEY:
                continue
            if kind == "anthropic" and not ANTHROPIC_API_KEY:
                continue
            print(f"\n--- {model_name} ({kind}) ---")
            await run_for_judge(client, model_name, kind, items, existing)

    print(f"\nDone. Total rows now in {OUT_FILE}:")
    print(f"  {sum(1 for _ in open(OUT_FILE))}")


if __name__ == "__main__":
    asyncio.run(main())
