"""LLMBar pilot: fresh vs batched-context judge administration.

Pilot question: does AMEL-style batched-context administration change
which judge model "wins" on LLMBar's Natural subset?

Design (3,000 calls):
    benchmark = LLMBar Natural subset (~100 pairwise items)
    judges    = GPT-4.1 Nano, Claude Haiku 4.5, Llama 3.2 3B
    regimes   = fresh-context per item; batched (50-item conversations)
    reps      = 5 per (judge, item, regime)
    T         = 1.0

Each LLMBar item is (instruction, output_1, output_2, label). The
judge sees both outputs in randomized A/B order and is asked which
is better. We compare the modal judge preference under fresh vs
batched administration on the same item.

Output: data/llmbar/pilot_responses.jsonl

Decision gate is in analysis/pilot_rankshift.py.
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

REPS = 5
BATCH_SIZE = 50  # number of items per batched conversation
TEMPERATURE = 1.0
MAX_TOKENS = 8  # judges only need to emit "1" or "2"

DATA_DIR = REPO / "data" / "llmbar"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ITEMS_FILE = DATA_DIR / "natural_items.json"
OUT_FILE = DATA_DIR / "pilot_responses.jsonl"

JUDGES = [
    ("gpt-4.1-nano", "openai"),
    ("claude-haiku-4-5", "anthropic"),
    ("llama3.2:3b", "ollama"),
]

SYSTEM_PROMPT = (
    "You compare two candidate responses to an instruction and decide which one "
    "better follows the instruction. Reply with a single character: 1 or 2."
)


# ---------- LLMBar item loading ------------------------------------

LLMBAR_NATURAL_URL = (
    "https://raw.githubusercontent.com/princeton-nlp/LLMBar/"
    "main/Dataset/LLMBar/Natural/dataset.json"
)


def load_llmbar_natural() -> list[dict]:
    """Load LLMBar Natural subset from the official GitHub source; cache locally.

    The princeton-nlp/LLMBar HuggingFace dataset uses a deprecated script
    loader, so I pull the raw dataset.json instead (same content)."""
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
            "label": int(r["label"]),  # 1 or 2: which output is preferred
        }
        for i, r in enumerate(raw)
    ]
    ITEMS_FILE.write_text(json.dumps(items, indent=2))
    print(f"Cached {len(items)} LLMBar Natural items to {ITEMS_FILE}")
    return items


def format_pairwise(instruction: str, resp_a: str, resp_b: str) -> str:
    return (
        f"Instruction:\n{instruction}\n\n"
        f"Response A:\n{resp_a}\n\n"
        f"Response B:\n{resp_b}\n\n"
        "Which response better follows the instruction? Reply 1 for A or 2 for B."
    )


def build_fresh_messages(item: dict, swap: bool) -> tuple[list[dict], str]:
    """Build a fresh conversation for one item. Returns messages + label-map
    indicating whether "1" in the response means output_1 or output_2."""
    if swap:
        resp_a, resp_b = item["output_2"], item["output_1"]
        truth = "1" if item["label"] == 2 else "2"
    else:
        resp_a, resp_b = item["output_1"], item["output_2"]
        truth = "1" if item["label"] == 1 else "2"
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": format_pairwise(item["input"], resp_a, resp_b)},
    ]
    return msgs, truth


def build_batched_messages(items_with_swap: list[tuple[dict, bool]], target_idx: int) -> tuple[list[dict], str]:
    """Build a batched conversation. Prior turns are the model's own judgments
    on earlier items (we use the LLMBar label as the assistant's prior reply,
    which simulates a long sequence of plausible judgments). The target_idx
    item is the last user turn; only its judgment is scored."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    truth = None
    for i, (item, swap) in enumerate(items_with_swap):
        if swap:
            resp_a, resp_b = item["output_2"], item["output_1"]
            label_after_swap = "1" if item["label"] == 2 else "2"
        else:
            resp_a, resp_b = item["output_1"], item["output_2"]
            label_after_swap = "1" if item["label"] == 1 else "2"
        msgs.append({"role": "user", "content": format_pairwise(item["input"], resp_a, resp_b)})
        if i < target_idx:
            msgs.append({"role": "assistant", "content": label_after_swap})
        else:
            truth = label_after_swap
            break
    return msgs, truth


# ---------- judge callers (one per provider) -----------------------

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
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
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


# ---------- parsing ------------------------------------------------

def parse_pairwise(text: str) -> str | None:
    """Return '1', '2', or None."""
    if not isinstance(text, str):
        return None
    for ch in text.strip():
        if ch == "1":
            return "1"
        if ch == "2":
            return "2"
    return None


# ---------- main loop ----------------------------------------------

async def run_for_judge(client, judge_name: str, provider: str, items: list[dict]) -> list[dict]:
    caller = CALLERS[provider]
    sem = asyncio.Semaphore(CONCURRENCY[provider])
    out: list[dict] = []

    # FRESH regime: each (item, rep) is one independent conversation
    print(f"  [{judge_name}] fresh regime: {len(items)} items x {REPS} reps")
    fresh_tasks = []
    for item in items:
        for rep in range(REPS):
            seed = abs(hash(f"{judge_name}|{item['id']}|fresh|{rep}")) & 0xFFFFFFFF
            swap = (seed & 1) == 1
            msgs, truth = build_fresh_messages(item, swap)
            fresh_tasks.append((item, rep, seed, swap, truth, msgs))

    async def fresh_one(item, rep, seed, swap, truth, msgs):
        text, elapsed = await caller(client, msgs, sem)
        return {
            "judge": judge_name,
            "regime": "fresh",
            "item_id": item["id"],
            "repetition": rep,
            "seed": seed,
            "swap": swap,
            "truth_label_after_swap": truth,
            "raw_response": text[:200] if isinstance(text, str) else "",
            "parsed_label": parse_pairwise(text),
            "response_time_ms": round(elapsed, 2),
        }

    done = 0
    for coro in asyncio.as_completed([fresh_one(*t) for t in fresh_tasks]):
        out.append(await coro)
        done += 1
        if done % 100 == 0:
            print(f"    fresh {done}/{len(fresh_tasks)}")

    # BATCHED regime: BATCH_SIZE items per conversation; we score only the
    # last item of each batch (the "target"). Each batch is repeated REPS
    # times with different item-orderings via seed.
    print(f"  [{judge_name}] batched regime: {len(items)} items x {REPS} reps "
          f"(batch_size={BATCH_SIZE})")
    batched_tasks = []
    for item in items:
        for rep in range(REPS):
            seed = abs(hash(f"{judge_name}|{item['id']}|batched|{rep}")) & 0xFFFFFFFF
            rng = random.Random(seed)
            # 49 random other items, then the target item last
            other_items = [it for it in items if it["id"] != item["id"]]
            prefix = rng.sample(other_items, k=BATCH_SIZE - 1)
            ordered = prefix + [item]
            # swap flag per turn, derived deterministically from seed
            swaps = [(rng.random() < 0.5) for _ in ordered]
            target_swap = swaps[-1]
            items_with_swap = list(zip(ordered, swaps))
            msgs, truth = build_batched_messages(items_with_swap, target_idx=len(ordered) - 1)
            batched_tasks.append((item, rep, seed, target_swap, truth, msgs))

    async def batched_one(item, rep, seed, swap, truth, msgs):
        text, elapsed = await caller(client, msgs, sem)
        return {
            "judge": judge_name,
            "regime": "batched",
            "item_id": item["id"],
            "repetition": rep,
            "seed": seed,
            "swap": swap,
            "truth_label_after_swap": truth,
            "raw_response": text[:200] if isinstance(text, str) else "",
            "parsed_label": parse_pairwise(text),
            "response_time_ms": round(elapsed, 2),
        }

    done = 0
    for coro in asyncio.as_completed([batched_one(*t) for t in batched_tasks]):
        out.append(await coro)
        done += 1
        if done % 100 == 0:
            print(f"    batched {done}/{len(batched_tasks)}")

    return out


async def main():
    items = load_llmbar_natural()
    print(f"Items loaded: {len(items)}")

    # Resume if file exists
    existing: set[tuple] = set()
    if OUT_FILE.exists():
        with open(OUT_FILE) as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                existing.add((r["judge"], r["regime"], r["item_id"], r["repetition"]))
        print(f"Resuming: {len(existing)} rows already in {OUT_FILE}")

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
            # filter out already-done
            rows = [r for r in rows if (r["judge"], r["regime"], r["item_id"], r["repetition"]) not in existing]
            with open(OUT_FILE, "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            print(f"  wrote {len(rows)} rows")

    print(f"\nDone. {OUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
