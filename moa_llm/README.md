# MOA-LLM — Movie Order Agent (LLM-optimized)

An optimized version of [`../moa.py`](../moa.py). Same job — type a franchise name
(e.g. `batman`) and get its movies/series ordered by **in-universe story
chronology** with a summary for each — but the unreliable parts are replaced by an
LLM call to the `ai.qestit.com` gateway for far more accurate ordering and cleaner
summaries.

## What changed vs `moa.py`

`moa.py` discovers real titles from Wikipedia/DuckDuckGo (solid), but then decides
the actual answer with brittle heuristics:

- **Ordering** — keyword scoring (`EARLY_STORY_WORDS`/`LATE_STORY_WORDS`), hand-tuned
  hacks (`joker → -100`), median-of-years. Frequently mis-orders reboots/prequels.
- **Summaries** — regex sentence-picking from raw Wikipedia text.

MOA-LLM **reuses the factual discovery unchanged** and replaces only those two steps
with a single, grounded LLM call:

- The LLM receives the *already-discovered real titles* plus an excerpt of each one's
  Wikipedia plot, and is told to **only reorder them** (indices are validated 1:1, so
  it can't invent, drop, or rename titles).
- It returns an in-universe chronological order, a story summary, a short "why here"
  reason per title, and an overall confidence.
- If the LLM is unavailable (no key, network/auth error, malformed response) it
  **falls back automatically** to `moa.py`'s heuristic ordering, so it always answers.

## Setup

1. Requires Python 3.10+. No third-party packages (standard library only).
2. Provide your gateway API key:

   ```sh
   cp moa_llm/.env.example moa_llm/.env
   # edit moa_llm/.env and set QESTIT_AI_API_KEY=...
   ```

   `.env` is gitignored — the key never enters version control. The key can also be
   supplied via a real environment variable (`QESTIT_AI_API_KEY`), which takes
   precedence and is useful in CI.

### Configuration (env vars / `.env`)

| Variable | Default | Notes |
|---|---|---|
| `QESTIT_AI_API_KEY` | _(required)_ | Bearer token for the gateway. |
| `MOA_LLM_MODEL` | `mdl_azure_gpt54` | Gateway model id (**internal slug, not an OpenAI name**). GPT 5.4. |
| `QESTIT_AI_BASE_URL` | `https://ai.qestit.com/openai/v1` | OpenAI-compatible base URL. |

Other models the gateway exposes: `mdl_azure_gpt54_mini` (cheaper), `mdl_azure_gpt52`,
`mdl_aws_claude_opus_48` (Claude Opus 4.8). List them with:

```sh
curl -s -H "Authorization: Bearer $QESTIT_AI_API_KEY" \
  https://ai.qestit.com/openai/v1/models
```

## Run

```sh
# one-shot
python moa_llm/moa_llm.py batman

# interactive REPL
python moa_llm/moa_llm.py
```

Compare against the original heuristic version to see the difference:

```sh
python moa.py batman
python moa_llm/moa_llm.py batman
```

## How it works (pipeline)

1. `moa.normalize_topic` → curated-guide fast path (`moa.match_guide`) if it hits.
2. `moa.discover_media` → real titles + sources (factual grounding).
3. `moa.fetch_plot` per title → plot excerpts.
4. One LLM call → ordered timeline + summaries + reasons (indices validated 1:1).
5. On any LLM failure → heuristic fallback (`moa.sort_by_story`).
