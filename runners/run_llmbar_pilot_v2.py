"""LLMBar pilot v2: corrected design.

Fixes three v1 problems flagged by analysis/pilot_diagnostics.py:

1. v1's batched regime fed LLMBar *ground-truth* labels as the prior
   assistant turns -- in-context learning from gold labels, NOT
   AMEL-style batched judging. v2 batches sequentially: the model
   judges each context item in turn, and its own parsed response
   becomes the next assistant turn.

2. v1 Haiku had 17% unparseable rate on fresh (verbose refusals) and
   went degenerate on batched (98% "2"). v2 uses a strict prompt
   ("respond with exactly one character: 1 or 2") and verifies
   parseability per call.

3. v1 batched used N=50 context turns. AMEL shows the effect
   saturates by N=5. v2 uses N=15 to keep cost down while remaining
   well into the saturated regime.

Total: 3 judges x 100 items x 3 reps x (1 fresh + 16 batched) =
14,400 batched + 900 fresh = 15,300 calls.
Cost ~$30-50 API; runtime ~4-6h (Llama is bottleneck).
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
OLLAMA_BASE_URL = "http://localhost:11434"

REPS = 3
CONTEXT_N = 15
TEMPERATURE = 1.0
MAX_TOKENS = 4
LLMBAR_NATURAL_URL = (
    "https://raw.githubusercontent.com/princeton-nlp/LLMBar/"
    "main/Dataset/LLMBar/Natural/dataset.json"
)

DATA_DIR = REPO / "data" / "llmbar"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ITEMS_FILE = DATA_DIR / "natural_items.json"
OUT_FILE = DATA_DIR / "pilot_v2_responses.jsonl"

JUDGES = [
    ("gpt-4.1-nano", "openai"),
    ("claude-haiku-4-5", "anthropic"),
    ("llama3.2:3b", "ollama"),
]

SYSTEM_PROMPT = (
    "You judge which of two candidate responses better follows an instruction. "
    "Respond with exactly one character: 1 if response A is better, 2 if "
    "response B is better. No other text, no explanation, no punctuation."
)


# ---------- LLMBar item loading ------------------------------------

def load_llmbar_natural() -> list[dict]:
    if ITEMS_FILE.exists():
        return json.loads(ITEMS_FILE.read_text())
    import urllib.request
    with urllib.request.urlopen(LLMBAR_NATURAL_URL, timeout=30) as r:
        raw = json.loads(r.read().decode("utf-8"))
    items = [
        {
            "id": f"natural_{i:03d}",
            "input": r["input"],
            "output_1": r["output_1"],
            "output_2": r["output_2"],
            "label": int(r["label"]),
        }
        for i, r in enumerate(raw)
    ]
    ITEMS_FILE.write_text(json.dumps(items, indent=2))
    return items


def format_pairwise(instruction: str, resp_a: str, resp_b: str) -> str:
    return (
        f"Instruction:\n{instruction}\n\n"
        f"Response A:\n{resp_a}\n\n"
        f"Response B:\n{resp_b}\n\n"
        "1 or 2?"
    )


def swap_pair(item: dict, swap: bool) -> tuple[str, str, str]:
    """Return (resp_a, resp_b, truth_label_after_swap)."""
    if swap:
        return (item["output_2"], item["output_1"],
                "1" if item["label"] == 2 else "2")
    return (item["output_1"], item["output_2"],
            "1" if item["label"] == 1 else "2")


# ---------- judge callers ------------------------------------------

async def call_openai(client, msgs, semaphore):
    async with semaphore:
        t0 = time.perf_counter()
        try:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json={
                    "model": "gpt-4.1-nano",
                    "messages": msgs,
                    "temperature": TEMPERATURE,
                    "max_tokens": MAX_TOKENS,
                },
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                timeout=60.0,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"], (time.perf_counter() - t0) * 1000
        except Exception as e:
            return f"ERROR: {e}", (time.perf_counter() - t0) * 1000


async def call_anthropic(client, msgs, semaphore):
    sys_content = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else SYSTEM_PROMPT
    body_msgs = [m for m in msgs if m["role"] != "system"]
    async with semaphore:
        t0 = time.perf_counter()
        try:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": "claude-haiku-4-5",
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
                timeout=60.0,
            )
            r.raise_for_status()
            blocks = r.json().get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text, (time.perf_counter() - t0) * 1000
        except Exception as e:
            return f"ERROR: {e}", (time.perf_counter() - t0) * 1000


async def call_ollama(client, msgs, semaphore):
    async with semaphore:
        t0 = time.perf_counter()
        try:
            r = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": "llama3.2:3b",
                    "messages": msgs,
                    "stream": False,
                    "options": {"temperature": TEMPERATURE, "num_predict": MAX_TOKENS},
                },
                timeout=600.0,
            )
            r.raise_for_status()
            return r.json()["message"]["content"], (time.perf_counter() - t0) * 1000
        except Exception as e:
            return f"ERROR: {e}", (time.perf_counter() - t0) * 1000


CALLERS = {"openai": call_openai, "anthropic": call_anthropic, "ollama": call_ollama}
CONCURRENCY = {"openai": 10, "anthropic": 5, "ollama": 2}


def parse_pairwise(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    for ch in text.strip():
        if ch == "1":
            return "1"
        if ch == "2":
            return "2"
    return None


# ---------- batched chain (sequential) -----------------------------

async def run_sequential_batch(
    client, caller, semaphore, judge_name, target_item, target_swap, target_rep,
    context_items: list[tuple[dict, bool]],
) -> dict:
    """Run a sequential batch: judge each context_item in turn, append the
    model's parsed answer (canonicalised to '1' / '2' / raw-on-fail) to the
    conversation, then judge the target item.

    Returns a row for the TARGET item only.
    """
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    context_log: list[dict] = []
    for ctx_item, ctx_swap in context_items:
        a, b, _truth = swap_pair(ctx_item, ctx_swap)
        msgs.append({"role": "user", "content": format_pairwise(ctx_item["input"], a, b)})
        raw, elapsed = await caller(client, msgs, semaphore)
        parsed = parse_pairwise(raw)
        # Canonical assistant turn: parsed label if available, else raw (truncated).
        # Using the parsed label keeps the conversation clean and matches what a
        # production pipeline that post-processes judge outputs would do.
        assistant_content = parsed if parsed in ("1", "2") else (raw or "?")[:8]
        msgs.append({"role": "assistant", "content": assistant_content})
        context_log.append({
            "item_id": ctx_item["id"],
            "swap": ctx_swap,
            "parsed": parsed,
            "raw_first8": (raw or "")[:8],
        })

    a, b, truth = swap_pair(target_item, target_swap)
    msgs.append({"role": "user", "content": format_pairwise(target_item["input"], a, b)})
    raw, elapsed = await caller(client, msgs, semaphore)
    parsed = parse_pairwise(raw)

    return {
        "judge": judge_name,
        "regime": "batched",
        "context_n": len(context_items),
        "item_id": target_item["id"],
        "repetition": target_rep,
        "swap": target_swap,
        "truth_label_after_swap": truth,
        "raw_response": raw[:200] if isinstance(raw, str) else "",
        "parsed_label": parsed,
        "response_time_ms": round(elapsed, 2),
        "context_log": context_log,  # for audit / debugging
    }


# ---------- per-judge runner ---------------------------------------

async def run_for_judge(client, judge_name, provider, items) -> list[dict]:
    caller = CALLERS[provider]
    sem = asyncio.Semaphore(CONCURRENCY[provider])
    out: list[dict] = []

    # FRESH ----------------------------------------------------------
    print(f"  [{judge_name}] fresh: {len(items)} items x {REPS} reps")
    fresh_tasks = []
    for item in items:
        for rep in range(REPS):
            seed = abs(hash(f"{judge_name}|{item['id']}|fresh_v2|{rep}")) & 0xFFFFFFFF
            swap = (seed & 1) == 1
            a, b, truth = swap_pair(item, swap)
            msgs = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": format_pairwise(item["input"], a, b)},
            ]
            fresh_tasks.append((item, rep, seed, swap, truth, msgs))

    async def fresh_one(item, rep, seed, swap, truth, msgs):
        raw, elapsed = await caller(client, msgs, sem)
        parsed = parse_pairwise(raw)
        return {
            "judge": judge_name,
            "regime": "fresh",
            "context_n": 0,
            "item_id": item["id"],
            "repetition": rep,
            "swap": swap,
            "truth_label_after_swap": truth,
            "raw_response": raw[:200] if isinstance(raw, str) else "",
            "parsed_label": parsed,
            "response_time_ms": round(elapsed, 2),
        }

    done = 0
    for coro in asyncio.as_completed([fresh_one(*t) for t in fresh_tasks]):
        out.append(await coro)
        done += 1
        if done % 100 == 0:
            print(f"    fresh {done}/{len(fresh_tasks)}")

    # BATCHED (sequential) -------------------------------------------
    print(f"  [{judge_name}] batched: {len(items)} items x {REPS} reps (N={CONTEXT_N})")
    batched_targets = []
    for item in items:
        for rep in range(REPS):
            seed = abs(hash(f"{judge_name}|{item['id']}|batched_v2|{rep}")) & 0xFFFFFFFF
            rng = random.Random(seed)
            others = [it for it in items if it["id"] != item["id"]]
            ctx_items = rng.sample(others, k=CONTEXT_N)
            ctx_swaps = [rng.random() < 0.5 for _ in ctx_items]
            target_swap = rng.random() < 0.5
            batched_targets.append((item, rep, target_swap,
                                    list(zip(ctx_items, ctx_swaps))))

    # Each batch is sequential within itself but multiple batches can run
    # concurrently up to the provider's concurrency cap.
    async def batched_one(target_item, target_rep, target_swap, ctx_items):
        return await run_sequential_batch(
            client, caller, sem, judge_name,
            target_item, target_swap, target_rep, ctx_items,
        )

    done = 0
    for coro in asyncio.as_completed([batched_one(*t) for t in batched_targets]):
        row = await coro
        out.append(row)
        # incremental write so progress is visible and resumable
        with open(OUT_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
        done += 1
        if done % 25 == 0:
            print(f"    batched {done}/{len(batched_targets)}")

    return out


async def main():
    items = load_llmbar_natural()
    print(f"Items loaded: {len(items)}")

    existing: set[tuple] = set()
    if OUT_FILE.exists():
        with open(OUT_FILE) as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                existing.add((r["judge"], r["regime"], r["item_id"], r["repetition"]))
        print(f"Resuming: {len(existing)} rows already present")

    async with httpx.AsyncClient() as client:
        for judge, provider in JUDGES:
            if provider == "openai" and not OPENAI_API_KEY:
                print(f"  Skipping {judge}: OPENAI_API_KEY not set")
                continue
            if provider == "anthropic" and not ANTHROPIC_API_KEY:
                print(f"  Skipping {judge}: ANTHROPIC_API_KEY not set")
                continue
            print(f"\n--- {judge} ({provider}) ---")
            rows = await run_for_judge(client, judge, provider, items)
            # only append fresh (batched were written incrementally above)
            fresh_new = [r for r in rows
                         if r["regime"] == "fresh"
                         and (r["judge"], r["regime"], r["item_id"], r["repetition"]) not in existing]
            with open(OUT_FILE, "a") as f:
                for r in fresh_new:
                    f.write(json.dumps(r) + "\n")
            print(f"  wrote {len(fresh_new)} fresh rows (batched rows written incrementally)")

    print(f"\nDone. {OUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
