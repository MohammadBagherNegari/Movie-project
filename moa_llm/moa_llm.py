#!/usr/bin/env python3
"""
MOA-LLM - Movie Order Agent, LLM-optimized edition.

Same idea as ../moa.py: type any franchise name (e.g. "batman") and get the
movies/series ordered by in-universe STORY chronology with a summary each.

The difference: the two brittle, heuristic steps in moa.py - story ordering
(analyze_story / sort_by_story) and summary extraction (make_story_summary) -
are replaced by a single grounded call to an LLM via ai.qestit.com. The factual
title discovery from moa.py is reused unchanged, so the LLM only ever reorders
real titles (no hallucinated entries) and writes summaries from real plot text.

If the LLM is unavailable (no key, network/auth error, bad response) the tool
transparently falls back to moa.py's original heuristic ordering, so it always
answers.

Run:  python moa_llm/moa_llm.py batman
      python moa_llm/moa_llm.py            # interactive REPL
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Reuse the original agent's discovery + heuristic fallback (no duplication).
# moa.py guards main() behind __name__ == "__main__", so importing only loads
# its functions.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import moa  # noqa: E402


# ---------------------------------------------------------------------------
# Config / environment
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """Load KEY=VALUE lines from moa_llm/.env into os.environ (if not already set).

    Tiny no-dependency parser so the local API key never has to live in tracked
    source. Real values belong in moa_llm/.env (gitignored).
    """
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_env()

API_KEY = os.environ.get("QESTIT_AI_API_KEY", "").strip()
BASE_URL = os.environ.get("QESTIT_AI_BASE_URL", "https://ai.qestit.com/openai/v1").rstrip("/")
# Gateway model ids are internal slugs (NOT OpenAI names like "gpt-4o").
# mdl_azure_gpt54 == GPT 5.4 (json_mode + structured_output capable).
MODEL = os.environ.get("MOA_LLM_MODEL", "mdl_azure_gpt54").strip()

LLM_TIMEOUT = 60
PLOT_GROUNDING_CHARS = 600


class LLMError(RuntimeError):
    """Raised on any failure that should trigger the heuristic fallback."""


# ---------------------------------------------------------------------------
# OpenAI-compatible client (stdlib urllib, matching moa.py's no-deps style)
# ---------------------------------------------------------------------------

def llm_chat(
    messages: list[dict],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2000,
    json_object: bool = True,
) -> str:
    """POST to {BASE_URL}/chat/completions and return the assistant message text.

    Retries transient errors with backoff. If the gateway rejects
    response_format (HTTP 400), retries once without it. Raises LLMError on any
    hard failure so the caller can fall back to the heuristic path.
    """
    if not API_KEY:
        raise LLMError("QESTIT_AI_API_KEY is not set (see moa_llm/.env.example)")

    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    def _payload(with_json: bool) -> bytes:
        body: dict = {
            "model": MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if with_json:
            body["response_format"] = {"type": "json_object"}
        return json.dumps(body).encode("utf-8")

    use_json = json_object
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, data=_payload(use_json), headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            choices = data.get("choices") or []
            if not choices:
                raise LLMError(f"LLM returned no choices: {str(data)[:300]}")
            content = (choices[0].get("message") or {}).get("content", "")
            if not content:
                raise LLMError("LLM returned empty content")
            return content
        except urllib.error.HTTPError as err:
            detail = ""
            try:
                detail = err.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001 - best-effort error detail only
                pass
            # response_format unsupported for this model -> retry once plain.
            if err.code == 400 and use_json:
                use_json = False
                last_err = LLMError(f"HTTP 400 (retrying without json mode): {detail}")
                continue
            last_err = LLMError(f"HTTP {err.code} from gateway: {detail}")
            if err.code in (401, 403, 404):
                break  # auth / bad model -> retrying won't help
            time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
            last_err = LLMError(f"LLM request failed: {err}")
            time.sleep(1.5 * (attempt + 1))

    raise last_err or LLMError("LLM request failed")


# ---------------------------------------------------------------------------
# Grounded ordering + summaries (single LLM call)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a movie-franchise continuity expert. You are given a list of real "
    "films/series that already exist, each with a release year and an excerpt of "
    "its plot. Order them strictly by IN-UNIVERSE STORY chronology (when events "
    "happen in the story world), NOT by release date. Ground every decision in the "
    "provided plot text and well-known canon. Write a concise, spoiler-light story "
    "summary for each. Do NOT invent, add, drop, merge, or rename any title - only "
    "reorder the ones given, using their exact index."
)


def _build_user_prompt(topic: str, items: list) -> str:
    lines = [
        f'Franchise/topic: "{topic}"',
        "",
        "Titles to order (index | title | release year | plot excerpt):",
    ]
    for i, item in enumerate(items):
        year = item.release_year if item.release_year else "unknown"
        excerpt = (item.plot or "").strip().replace("\n", " ")
        excerpt = excerpt[:PLOT_GROUNDING_CHARS] if excerpt else "(no plot text found)"
        lines.append(f"{i} | {item.title} | {year} | {excerpt}")
    lines += [
        "",
        "Respond with ONLY a JSON object of this exact shape:",
        '{',
        '  "order": [',
        '    {"index": <int from the list above>, "story_year": <int or null>,',
        '     "summary": "<1-3 sentence story summary>",',
        '     "reason": "<short why-it-goes-here note>"}',
        '  ],',
        '  "confidence": "high" | "medium" | "low"',
        '}',
        "",
        "Rules: include EVERY index above exactly once, earliest in-universe story "
        "first. story_year is the approximate in-universe year if known, else null.",
    ]
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Parse a JSON object, tolerating prose/code-fences around it."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as err:
            raise LLMError(f"Could not parse LLM JSON: {err}") from err
    raise LLMError("LLM response contained no JSON object")


def llm_order_and_summarize(topic: str, titles: list) -> tuple[list, str]:
    """Fetch grounding plots, ask the LLM to order + summarize, return (ordered, confidence).

    Validates that the LLM's indices map 1:1 onto the input titles; raises
    LLMError otherwise so the caller falls back to the heuristic path.
    """
    items = list(titles)
    total = len(items)
    for i, item in enumerate(items, 1):
        print(f"  Fetching story summary {i}/{total}: {item.title}...", file=sys.stderr)
        item.plot = moa.fetch_plot(item.title, item.release_year)

    print("  Asking the LLM to order the timeline...", file=sys.stderr)
    content = llm_chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(topic, items)},
    ])
    parsed = _extract_json(content)

    order = parsed.get("order")
    if not isinstance(order, list) or not order:
        raise LLMError("LLM JSON missing a non-empty 'order' array")

    ordered: list = []
    used: set[int] = set()
    for entry in order:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("index")
        if not isinstance(idx, int) or not (0 <= idx < total) or idx in used:
            continue
        used.add(idx)
        item = items[idx]
        story_year = entry.get("story_year")
        item.story_year = float(story_year) if isinstance(story_year, (int, float)) else None
        item.llm_summary = str(entry.get("summary", "")).strip()
        item.llm_reason = str(entry.get("reason", "")).strip()
        ordered.append(item)

    if len(used) != total:
        raise LLMError(
            f"LLM ordering covered {len(used)}/{total} titles; "
            "indices did not map 1:1 onto the discovered titles"
        )

    confidence = str(parsed.get("confidence", "")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "high" if total >= 5 else "medium" if total >= 2 else "low"
    return ordered, confidence


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def format_llm_plan(topic: str, titles: list, sources: list[str], confidence: str) -> str:
    lines = [
        f'Story watch order for: "{topic}"',
        f"Found {len(titles)} movies/series - ordered by STORY (not release date)",
        f"Ordering by: LLM ({MODEL}), grounded on web sources",
        f"Confidence: {confidence}",
        "",
    ]
    if sources:
        lines.append("Discovered from:")
        for s in sources[:6]:
            lines.append(f"  - {s}")
        lines.append("")

    lines.append("=== Chronological Story Order ===")
    lines.append("")
    for i, item in enumerate(titles, 1):
        yr = ""
        if item.story_year:
            yr = f" [story ~{int(item.story_year)}]"
        elif item.release_year:
            yr = f" [released {item.release_year}]"
        type_tag = f" ({item.media_type})" if item.media_type != "movie" else ""
        lines.append(f"{i}. {item.title}{type_tag}{yr}")
        summary = getattr(item, "llm_summary", "") or "(Summary not available)"
        lines.append(f"   Summary: {summary}")
        reason = getattr(item, "llm_reason", "")
        if reason:
            lines.append(f"   Why here: {reason}")
        lines.append("")

    lines.append(
        "Note: Order is reasoned by an LLM from plot descriptions discovered on the "
        "web. Reboots and alternate timelines are placed by in-universe story; use "
        "your judgment for standalone spin-offs."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main pipeline (LLM-first, heuristic fallback)
# ---------------------------------------------------------------------------

def build_watch_order_llm(query: str) -> str:
    topic = moa.normalize_topic(query)
    if not topic:
        return "Please type a name - e.g. batman, star wars, harry potter"

    # Curated fast path (same as moa.py).
    guide = moa.match_guide(topic, moa.load_guides())
    if guide:
        return moa.plan_from_guide(topic, guide)

    print(f'  Looking up all "{topic}" movies and series...', file=sys.stderr)
    titles, sources = moa.discover_media(topic)

    if not titles:
        print("  No filmography found, searching watch-order guides...", file=sys.stderr)
        return moa._fallback_watch_order_guides(topic)

    print(f"  Found {len(titles)} titles.", file=sys.stderr)
    try:
        ordered, confidence = llm_order_and_summarize(topic, titles)
        return format_llm_plan(topic, ordered, sources, confidence)
    except LLMError as err:
        print(f"  LLM unavailable ({err}); using heuristic ordering.", file=sys.stderr)
        sorted_titles = moa.sort_by_story(titles)
        return moa.format_story_plan(topic, sorted_titles, sources)


def interactive() -> None:
    print("=" * 55)
    print("  MOA-LLM - Movie Order Agent (LLM-optimized)")
    print("  Type any name. We find the movies/series and order")
    print("  them by STORY, reasoned by an LLM with a summary each.")
    print("=" * 55)
    print()
    print(f"  Model: {MODEL}   Key: {'set' if API_KEY else 'MISSING (heuristic fallback)'}")
    print()
    print("Examples:  batman   superman   bond   trek   harry potter")
    print("Type quit to exit.")
    print()

    while True:
        try:
            query = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        print()
        print(build_watch_order_llm(query))
        print()
        print("-" * 55)
        print()


def main() -> None:
    if len(sys.argv) > 1:
        print(build_watch_order_llm(" ".join(sys.argv[1:])))
    else:
        interactive()


if __name__ == "__main__":
    main()
