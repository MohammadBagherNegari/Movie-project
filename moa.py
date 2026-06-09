#!/usr/bin/env python3
"""
MOA - Movie Order Agent
Type any name (e.g. "batman") - discovers movies/series and orders by STORY.
Run: python moa.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

USER_AGENT = "MOA/1.0 (watch-order-research; educational)"
TIMEOUT = 14
_MIN_FETCH_INTERVAL = 0.75
_last_fetch_at = 0.0
MAX_PAGES = 4
GUIDES_DIR = Path(__file__).parent / "guides"

FILLER_PHRASES = [
    r"give me", r"chronological order", r"watch order", r"in order",
    r"story order", r"timeline", r"what order", r"how to watch",
    r"grouped by", r"please", r"show me", r"list of", r"all the",
    r"connections", r"in story order", r"in-universe",
]

EARLY_STORY_WORDS = [
    "origin", "origins", "begins", "early life", "childhood", "year one",
    "how he became", "how she became", "first adventure", "rise of",
    "before becoming", "formation", "created", "introduces", "debut",
    "younger", "kid", "boy", "girl", "training begins", "becomes",
]

LATE_STORY_WORDS = [
    "years later", "after the events", "following the", "sequel",
    "returns", "resurrection", "finale", "concludes", "final battle",
    "showdown", "revenge", "after defeating", "after the death",
    "legacy", "passes the torch", "elderly", "older", "retired",
]

TITLE_EARLY = ["begins", "origins", "year one", "masks", "creation", "first"]
TITLE_LATE = ["returns", "rises", "forever", "vs.", " v ", "strikes again",
              "resurrection", "awakens", "endgame", "finale", "last"]


@dataclass
class MediaTitle:
    title: str
    media_type: str = "movie"
    release_year: int | None = None
    story_year: float | None = None
    story_score: float = 0.0
    plot: str = ""
    source: str = ""


# ---------------------------------------------------------------------------
# Input - accept ANY text, pull out the topic word(s)
# ---------------------------------------------------------------------------

def normalize_topic(query: str) -> str:
    q = query.lower().strip()
    for phrase in FILLER_PHRASES:
        q = re.sub(phrase, " ", q)
    q = re.sub(r"\b(movies?|films?|series|shows?|tv|animation)\b", " ", q)
    q = re.sub(r"[^\w\s'-]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


# ---------------------------------------------------------------------------
# HTTP / Wikipedia
# ---------------------------------------------------------------------------

def fetch(url: str, method: str = "GET", data: bytes | None = None) -> str:
    global _last_fetch_at
    headers = {"User-Agent": USER_AGENT}
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    last_err: Exception | None = None
    for attempt in range(3):
        elapsed = time.time() - _last_fetch_at
        if elapsed < _MIN_FETCH_INTERVAL:
            time.sleep(_MIN_FETCH_INTERVAL - elapsed)
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                _last_fetch_at = time.time()
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as err:
            last_err = err
            time.sleep(1.5 * (attempt + 1))
    if last_err:
        raise last_err
    return ""


def clean_plot_text(text: str) -> str:
    text = re.sub(r"\[?\s*edit\s*\]?", "", text, flags=re.I)
    text = re.sub(r"^(Plot|Synopsis|Premise)\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_html(html: str) -> str:
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p>|</li>|</h[1-6]>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&[a-z]+;|&#\d+;", " ", html, flags=re.I)
    html = re.sub(r"[ \t]+", " ", html)
    return html


def search_wikipedia(query: str, limit: int = 6) -> list[str]:
    params = urllib.parse.urlencode({
        "action": "opensearch", "search": query, "limit": str(limit), "format": "json",
    })
    try:
        data = json.loads(fetch(f"https://en.wikipedia.org/w/api.php?{params}"))
        return data[1] if len(data) > 1 else []
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return []


def wiki_search_pages(query: str, limit: int = 5) -> list[str]:
    """More reliable than opensearch for finding film articles."""
    params = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "format": "json", "srlimit": str(limit),
    })
    try:
        data = json.loads(fetch(f"https://en.wikipedia.org/w/api.php?{params}"))
        return [r["title"] for r in data.get("query", {}).get("search", [])]
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return []


def get_wikipedia_text(title: str, sentences: int = 0) -> str:
    params = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": "true",
        "redirects": "1", "titles": title, "format": "json",
    })
    try:
        data = json.loads(fetch(f"https://en.wikipedia.org/w/api.php?{params}"))
        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        if page.get("missing"):
            return ""
        text = page.get("extract", "")
        if sentences > 0 and text:
            parts = re.split(r"(?<=[.!?])\s+", text)
            text = " ".join(parts[:sentences])
        return text
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return ""


_plot_cache: dict[str, str] = {}

META_SENTENCE_RE = re.compile(
    r"\b(directed by|co-wrote|written by|stars?|starring|featuring|produced by|"
    r"screenplay|supporting roles|ensemble cast|distributed by|box office)\b",
    re.I,
)
STORY_SENTENCE_RE = re.compile(
    r"\b(after|when|must|defeat|discovers|fights?|battles?|escapes?|saves?|villain|"
    r"threat|gotham|city|world|kill|murder|revenge|return|confront|stop|destroy|"
    r"investigat|serial|crime|corrupt|team|league|plan|secret|identity)\b",
    re.I,
)


def resolve_wiki_page(title: str) -> str:
    """Return canonical Wikipedia page title for a search string."""
    params = urllib.parse.urlencode({
        "action": "query", "titles": title, "redirects": "1", "format": "json",
    })
    try:
        data = json.loads(fetch(f"https://en.wikipedia.org/w/api.php?{params}"))
        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        if page.get("missing"):
            return ""
        return page.get("title", "")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return ""


def get_wikipedia_plot_section(page_title: str) -> str:
    """Fetch the Plot / Synopsis / Premise section from a Wikipedia article."""
    if not page_title:
        return ""
    params = urllib.parse.urlencode({
        "action": "parse", "page": page_title, "prop": "sections", "format": "json",
    })
    try:
        data = json.loads(fetch(f"https://en.wikipedia.org/w/api.php?{params}"))
        sections = data.get("parse", {}).get("sections", [])
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return ""

    plot_idx = None
    for sec in sections:
        line = sec.get("line", "").lower()
        if any(k in line for k in ("plot", "synopsis", "premise", "storyline")):
            plot_idx = sec.get("index")
            break

    if not plot_idx:
        return ""

    params2 = urllib.parse.urlencode({
        "action": "parse", "page": page_title, "section": str(plot_idx),
        "prop": "text", "format": "json",
    })
    try:
        data = json.loads(fetch(f"https://en.wikipedia.org/w/api.php?{params2}"))
        html = data.get("parse", {}).get("text", {}).get("*", "")
        return strip_html(html)[:3000]
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return ""


def _title_words_match(page_title: str, film_title: str) -> bool:
    stop = {"the", "a", "an", "of", "and", "in", "to", "for"}
    film_words = {w for w in re.findall(r"\w+", film_title.lower()) if w not in stop}
    page_words = {w for w in re.findall(r"\w+", page_title.lower()) if w not in stop}
    if not film_words:
        return False
    overlap = len(film_words & page_words)
    return overlap >= max(1, len(film_words) - 1)


def search_duckduckgo(query: str) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    body = urllib.parse.urlencode({"q": query}).encode()
    try:
        html = fetch("https://html.duckduckgo.com/html/", method="POST", data=body)
    except (urllib.error.URLError, TimeoutError, OSError):
        return results

    links = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', html, re.I,
    )
    snippets = re.findall(
        r'<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)</a>', html, re.I,
    )
    seen: set[str] = set()
    for i, (href, title_html) in enumerate(links[:12]):
        url = href
        if url.startswith("//duckduckgo.com/l/?"):
            parsed = urllib.parse.urlparse("https:" + url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                url = urllib.parse.unquote(qs["uddg"][0])
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        title = strip_html(title_html).strip()
        snippet = strip_html(snippets[i]).strip() if i < len(snippets) else ""
        results.append((title, url, snippet))
    return results


# ---------------------------------------------------------------------------
# Discover all movies & series for a topic
# ---------------------------------------------------------------------------

SKIP_TITLES = {
    "see also", "references", "external links", "contents", "navigation",
    "edit", "category", "main article", "further reading", "notes",
    "episodes", "cast", "production", "reception", "home media",
    "cast members", "speculation", "press", "sequel to", "following",
}

BAD_TITLE_RE = [
    re.compile(r"speculation|press |cast member|^\s*in\s+", re.I),
    re.compile(r"^\w+,\s+\w+$"),
    re.compile(r"sequel to|main article|fictional character", re.I),
]

JUNK_IN_TITLE = [
    "game boy", "playstation", "xbox", "gamecube", "nintendo", "atari", "game gear",
    "voiced by", "director", "cited", "appears in", "based on", "comic book",
    "story arc", "then released", "then ", "including", "portray", "actor.",
    "working on", "since dc", "media.", "spin-off", "r-rated", "film batman",
    "and another", "after working", "complete experience", "playable character",
    "video game", "lego dimensions", "littlebigplanet", "scribblenauts",
    "unchained", "impostors", "videogame", "self-titled",
    "toggle", "subsection", "weyland", "o'bannon", "original work",
    "can exploit", "rottentomatoes",
]

MAX_TITLES_TO_ORDER = 30


def _clean_title(raw: str) -> str:
    t = re.sub(r"\[\d+\]", "", raw)
    t = re.sub(r"\s+", " ", t).strip(" \"'*#")
    return t


def _guess_media_type(title: str, context: str) -> str:
    combined = (title + " " + context).lower()
    if any(w in combined for w in ("tv series", "television series", "miniseries", "animated series")):
        return "series"
    if any(w in combined for w in ("animated", "animation")):
        return "animation"
    return "movie"


# Spin-offs that belong to a franchise but don't include the topic word
FRANCHISE_RELATED: dict[str, list[str]] = {
    "batman": ["dark knight", "gotham", "joker", "robin", "catwoman", "phantasm",
               "suicide squad", "justice league", "batgirl", "riddler", "penguin"],
    "superman": ["man of steel", "justice league", "supergirl"],
    "spider": ["spider-man", "spiderman", "venom", "morbius"],
    "marvel": ["avengers", "iron man", "thor", "hulk", "ant-man", "guardians"],
    "bond": ["007", "james bond"],
    "trek": ["star trek"],
    "wars": ["star wars", "mandalorian"],
}


def _is_related(title: str, topic: str) -> bool:
    lower = title.lower()
    topic_l = topic.lower()
    if topic_l in lower:
        return True
    for key, related in FRANCHISE_RELATED.items():
        if key in topic_l or topic_l in key:
            if any(r in lower for r in related):
                return True
    return False


def _normalize_extracted_title(title: str) -> str:
    """Pull real film name out of sentence fragments."""
    if ": " in title:
        parts = [p.strip() for p in title.split(": ")]
        # Prefer last segment that looks like a title (starts with The/Batman/etc.)
        for part in reversed(parts):
            if re.match(r"^[A-Z][A-Za-z]", part) and len(part.split()) <= 8:
                title = part
                break
    # Trim trailing sentence junk
    title = re.sub(r"\s+(voiced by|appears in|beginning with).*$", "", title, flags=re.I)
    return _clean_title(title)


def _is_valid_media(title: str, topic: str, *, from_filmography: bool = False) -> bool:
    title = _normalize_extracted_title(title)
    if len(title) < 4 or len(title) > 65:
        return False
    words = title.split()
    if len(words) > 8:
        return False
    lower = title.lower()
    if any(s in lower for s in SKIP_TITLES):
        return False
    if any(j in lower for j in JUNK_IN_TITLE):
        return False
    if any(p.search(title) for p in BAD_TITLE_RE):
        return False
    if lower in ("the", "a", "film", "movie", "series", "james"):
        return False
    # Skip unrelated franchise entries unless clearly tied to topic
    if "superman" in lower and topic not in ("superman", "dc", "justice"):
        return False
    if "spider-man" in lower and "spider" not in topic.lower():
        return False
    if "avengers" in lower and topic not in ("marvel", "avengers", "mcu"):
        return False
    # Reject sentence fragments (too many lowercase words in the middle)
    if sum(1 for w in words[1:-1] if w.islower() and len(w) > 3) > 2:
        return False
    if _is_related(title, topic):
        return True
    if from_filmography and re.match(r"^The [A-Z]", title):
        return True
    return False


def _filmography_text_only(text: str) -> str:
    """Keep intro + live-action/animated sections; skip video games etc."""
    chunks = [text.split("\n==")[0]]  # intro before first section
    for block in re.split(r"\n==+ ", text):
        header = block.split("\n")[0].lower()
        if any(skip in header for skip in [
            "video game", "cast", "box office", "reception", "see also",
            "references", "external link", "merchandise", "theme park",
        ]):
            continue
        if any(keep in header for keep in [
            "live-action", "animated", "initial", "early", "theatrical",
            "television", "serial", "film", "dceu", "dark knight",
        ]) or header.strip() == "":
            chunks.append(block)
    return "\n".join(chunks)[:12_000]


def extract_titles_from_text(text: str, topic: str, *, from_filmography: bool = False) -> list[MediaTitle]:
    found: list[MediaTitle] = []
    seen: set[str] = set()

    def _add(title: str, year_str: str | None, context: str) -> None:
        title = _normalize_extracted_title(title)
        if not _is_valid_media(title, topic, from_filmography=from_filmography):
            return
        key = title.lower()
        if key in seen:
            return
        if year_str and not (1920 <= int(year_str) <= 2035):
            return
        seen.add(key)
        found.append(MediaTitle(
            title=title,
            media_type=_guess_media_type(title, context),
            release_year=int(year_str) if year_str else None,
        ))

    # "Title (1989 film)" from Wikipedia lists
    film_pat = re.compile(
        r"([A-Z][A-Za-z0-9':,&\-\s]{2,55}?)\s*\((\d{4})\s*"
        r"(?:film|movie|television series|TV series|animated film|animated series)[^)]*\)",
        re.I,
    )
    for m in film_pat.finditer(text):
        _add(m.group(1), m.group(2), m.group(0))

    # Prose style: "Batman Begins (2005)," in Wikipedia paragraphs
    prose_pat = re.compile(
        r"(?<![\w])([A-Z][A-Za-z0-9':\-&\.\s]{2,55}?)\s*\((\d{4})\)"
    )
    for m in prose_pat.finditer(text):
        _add(m.group(1), m.group(2), m.group(0))

    # Secondary: numbered lists "1. Title (1989)"
    for line in text.split("\n"):
        line = _clean_title(line)
        m = re.match(r"^\s*\d+[.)]\s+([A-Z][A-Za-z0-9':,&\-\s]{2,50}?)(?:\s*\((\d{4})\))?", line)
        if not m:
            continue
        _add(m.group(1), m.group(2), line)

    return found


def discover_media(topic: str) -> tuple[list[MediaTitle], list[str]]:
    all_titles: list[MediaTitle] = []
    seen: set[str] = set()
    sources: list[str] = []

    wiki_searches = [
        f"{topic} in film",
        f"List of {topic} films",
        f"{topic} filmography",
        f"{topic} (franchise)",
        f"{topic} in television",
        f"List of {topic} media",
    ]

    for wq in wiki_searches:
        for page in search_wikipedia(wq)[:2]:
            if "cast member" in page.lower():
                continue
            text = _filmography_text_only(get_wikipedia_text(page))
            if not text:
                continue
            items = extract_titles_from_text(text, topic, from_filmography=True)
            if items:
                src = f"Wikipedia: {page}"
                if src not in sources:
                    sources.append(src)
                for item in items:
                    key = item.title.lower()
                    if key not in seen:
                        seen.add(key)
                        item.source = src
                        all_titles.append(item)

    pages_fetched = 0
    for _, url, snippet in search_duckduckgo(f"{topic} films movies tv series complete list"):
        if pages_fetched >= MAX_PAGES:
            break
        snippet_items = extract_titles_from_text(snippet, topic, from_filmography=False)
        for item in snippet_items:
            key = item.title.lower()
            if key not in seen:
                seen.add(key)
                item.source = url
                all_titles.append(item)
        try:
            html = fetch(url)
            pages_fetched += 1
            page_items = extract_titles_from_text(strip_html(html[:120_000]), topic, from_filmography=False)
            if page_items and url not in sources:
                sources.append(url)
            for item in page_items:
                key = item.title.lower()
                if key not in seen:
                    seen.add(key)
                    item.source = url
                    all_titles.append(item)
        except (urllib.error.URLError, TimeoutError, OSError):
            pass

    # Direct fetch of the main filmography article
    if len(all_titles) < 3:
        topic_title = topic.strip().title()
        for direct in [f"{topic_title} in film", f"List of {topic_title} films", f"{topic_title} (franchise)"]:
            text = _filmography_text_only(get_wikipedia_text(direct))
            if text:
                items = extract_titles_from_text(text, topic, from_filmography=True)
                src = f"Wikipedia: {direct}"
                if items and src not in sources:
                    sources.append(src)
                for item in items:
                    key = item.title.lower()
                    if key not in seen:
                        seen.add(key)
                        item.source = src
                        all_titles.append(item)

    # If still few results, search individual film pages
    if len(all_titles) < 3:
        for page in search_wikipedia(f"{topic} film")[:5]:
            text = get_wikipedia_text(page)
            # Single film page - use page title
            m = re.match(r"^(.+?)\s*\(\d{4}\s+film\)", page, re.I)
            if m:
                t = _clean_title(m.group(1))
                if t.lower() not in seen:
                    seen.add(t.lower())
                    yr = re.search(r"\((\d{4})", page)
                    all_titles.append(MediaTitle(
                        title=t,
                        release_year=int(yr.group(1)) if yr else None,
                        source=f"Wikipedia: {page}",
                    ))

    # Deduplicate: keep shortest title variant per release year + fuzzy name
    deduped: list[MediaTitle] = []
    by_key: dict[str, MediaTitle] = {}
    for item in all_titles:
        key = re.sub(r"[^a-z0-9]", "", item.title.lower())
        if item.release_year:
            key += str(item.release_year)
        existing = by_key.get(key)
        if not existing or len(item.title) < len(existing.title):
            by_key[key] = item
    deduped = list(by_key.values())

    return deduped[:MAX_TITLES_TO_ORDER], sources


# ---------------------------------------------------------------------------
# Story analysis from plot descriptions
# ---------------------------------------------------------------------------

def _clean_film_title(title: str) -> str:
    """Keep (year) for short/ambiguous names like 'Batman (1989)'."""
    title = title.strip()
    base = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
    if len(base.split()) <= 2:
        return title
    return base


def _release_year_from_title(title: str) -> int | None:
    m = re.search(r"\((\d{4})\)\s*$", title)
    return int(m.group(1)) if m else None


def fetch_plot(title: str, release_year: int | None = None) -> str:
    """Fetch plot/synopsis text from Wikipedia for story summaries."""
    cache_key = f"{title}|{release_year}"
    if cache_key in _plot_cache:
        return _plot_cache[cache_key]

    clean = _clean_film_title(title)
    year = release_year or _release_year_from_title(title)

    candidates: list[str] = []
    if year:
        candidates.extend([
            f"{clean} ({year} film)",
            f"{clean} ({year} movie)",
            f"{clean} ({year})",
        ])
    candidates.extend([f"{clean} (film)", clean, title])

    seen_pages: set[str] = set()
    for candidate in candidates:
        page = resolve_wiki_page(candidate)
        if not page or page in seen_pages:
            continue
        seen_pages.add(page)
        pl = page.lower()
        if pl.startswith("list of") or "disambiguation" in pl:
            continue
        if "video game" in pl or "soundtrack" in pl or "cast member" in pl:
            continue

        plot = clean_plot_text(get_wikipedia_plot_section(page))
        if len(plot) > 80:
            _plot_cache[cache_key] = plot
            return plot

        intro = clean_plot_text(get_wikipedia_text(page))
        if len(intro) > 80:
            _plot_cache[cache_key] = intro
            return intro

    for candidate in candidates:
        for page in wiki_search_pages(candidate, limit=6):
            if page in seen_pages:
                continue
            pl = page.lower()
            if pl.startswith("list of") or "disambiguation" in pl:
                continue
            if "video game" in pl or "soundtrack" in pl:
                continue
            if not _title_words_match(page, clean):
                continue
            seen_pages.add(page)
            plot = clean_plot_text(get_wikipedia_plot_section(page))
            if len(plot) > 80:
                _plot_cache[cache_key] = plot
                return plot
            intro = clean_plot_text(get_wikipedia_text(page))
            if len(intro) > 80:
                _plot_cache[cache_key] = intro
                return intro

    for page in search_wikipedia(f"{clean} film"):
        if page in seen_pages or not _title_words_match(page, clean):
            continue
        plot = clean_plot_text(get_wikipedia_plot_section(page))
        if len(plot) > 80:
            _plot_cache[cache_key] = plot
            return plot
        intro = clean_plot_text(get_wikipedia_text(page))
        if len(intro) > 80:
            _plot_cache[cache_key] = intro
            return intro

    _plot_cache[cache_key] = ""
    return ""


def analyze_story(title: str, plot: str, release_year: int | None) -> tuple[float, float | None]:
    """
    Returns (story_score for sorting, inferred in-universe year if found).
    Lower story_score = earlier in the story.
    """
    combined = f"{title} {plot}".lower()
    score = 0.0
    story_year: float | None = None

    # Years mentioned in the plot = in-universe dates
    years = [int(y) for y in re.findall(r"\b(18\d{2}|19\d{2}|20[0-3]\d)\b", plot)]
    if years:
        story_year = float(statistics.median(years))
        score = story_year

    # Title signals (works even when plot is missing)
    t_lower = title.lower()
    for kw in TITLE_EARLY:
        if kw in t_lower:
            score -= 80
    for kw in TITLE_LATE:
        if kw in t_lower:
            score += 40
    if "joker" in t_lower and "batman" not in t_lower:
        score -= 100  # Joker origin often earliest in that timeline

    # Plot content signals
    for kw in EARLY_STORY_WORDS:
        if kw in combined:
            score -= 25
    for kw in LATE_STORY_WORDS:
        if kw in combined:
            score += 25

    if "prequel" in combined:
        score -= 60
    if "flashback" in combined:
        score -= 20
    if "set after" in combined or "takes place after" in combined:
        score += 35
    if "set before" in combined or "takes place before" in combined:
        score -= 35

    # "N years later"
    later = re.search(r"(\d+)\s+years?\s+later", combined)
    if later:
        score += int(later.group(1)) * 2

    # Without in-universe year, use release year only as last-resort tiebreaker
    if story_year is None and release_year and not plot:
        score += release_year * 0.01

    return score, story_year


def sort_by_story(titles: list[MediaTitle]) -> list[MediaTitle]:
    enriched: list[MediaTitle] = []
    total = len(titles)
    for i, item in enumerate(titles, 1):
        print(f"  Fetching story summary {i}/{total}: {item.title}...", file=sys.stderr)
        plot = fetch_plot(item.title, item.release_year)
        item.plot = plot
        item.story_score, item.story_year = analyze_story(
            item.title, plot, item.release_year
        )
        enriched.append(item)

    enriched.sort(key=lambda x: (x.story_score, x.release_year or 9999))
    return enriched


# ---------------------------------------------------------------------------
# Curated guides (optional fast path)
# ---------------------------------------------------------------------------

def load_guides() -> list[dict]:
    guides = []
    if not GUIDES_DIR.exists():
        return guides
    for path in sorted(GUIDES_DIR.glob("*.json")):
        try:
            guides.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return guides


def match_guide(topic: str, guides: list[dict]) -> dict | None:
    topic_l = topic.lower().strip()
    for guide in guides:
        names = [guide.get("name", "").lower()] + [a.lower() for a in guide.get("aliases", [])]
        for name in names:
            if not name:
                continue
            # Single word "batman" matches alias "batman"
            if topic_l == name or topic_l == name.split()[0]:
                return guide
            if name in topic_l or topic_l in name:
                return guide
    return None


def plan_from_guide(topic: str, guide: dict) -> str:
    lines = [
        f'Story watch order for: "{topic}"',
        "Confidence: high (curated guide)",
        "",
        "=== In-Universe Order ===",
        "",
    ]
    rank = 1
    total = sum(len(c.get("items", [])) for c in guide.get("clusters", []))
    idx = 0
    for cluster in guide.get("clusters", []):
        lines.append(f"--- {cluster['name']} ---")
        lines.append(cluster.get("reason", ""))
        for item in cluster.get("items", []):
            idx += 1
            print(f"  Fetching story summary {idx}/{total}: {item['title']}...", file=sys.stderr)
            yr = f" (story ~{item['year']})" if item.get("year") else ""
            note = f" - {item['note']}" if item.get("note") else ""
            lines.append(f"  {rank}. {item['title']}{yr}{note}")
            summary = _story_line_for_item(
                item["title"],
                _guide_release_year(item),
                preset=item.get("summary", ""),
            )
            lines.append(f"     Summary: {summary}")
            rank += 1
        lines.append("")

    skip_list = guide.get("skipList", [])
    if skip_list:
        lines.append("=== Optional Skips ===")
        for s in skip_list:
            lines.append(f"- {s['title']}: {s['reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def make_story_summary(plot: str, max_chars: int = 400) -> str:
    """Turn raw Wikipedia plot/intro into a readable story summary."""
    if not plot:
        return "(Summary not available - try a more specific franchise name)"

    text = re.sub(r"\s+", " ", plot).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    # Drop opening metadata line: "X is a 2022 American superhero film..."
    start = 0
    if sentences and re.search(
        r"\bis a\b.{0,40}\b(film|movie|series|show|superhero|thriller|drama)\b", sentences[0], re.I
    ):
        start = 1

    picked: list[str] = []
    total = 0
    for sent in sentences[start:]:
        if META_SENTENCE_RE.search(sent) and not STORY_SENTENCE_RE.search(sent):
            continue
        if total + len(sent) > max_chars and picked:
            break
        picked.append(sent)
        total += len(sent)
        if len(picked) >= 3:
            break

    if not picked:
        for sent in sentences[start:]:
            if META_SENTENCE_RE.search(sent):
                continue
            picked.append(sent)
            if len(picked) >= 2:
                break
    if not picked:
        picked = sentences[:2] if sentences else [text[:max_chars]]

    summary = " ".join(picked)
    summary = re.sub(r"^Plot\s+", "", summary, flags=re.I)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rsplit(" ", 1)[0] + "..."
    return summary


def _plot_summary(plot: str) -> str:
    return make_story_summary(plot)


def _guide_release_year(item: dict) -> int | None:
    if item.get("releaseYear"):
        return int(item["releaseYear"])
    return _release_year_from_title(item.get("title", ""))


def _story_line_for_item(title: str, release_year: int | None, preset: str = "") -> str:
    if preset:
        return preset
    plot = fetch_plot(title, release_year)
    return make_story_summary(plot)


def format_story_plan(topic: str, titles: list[MediaTitle], sources: list[str]) -> str:
    if not titles:
        return (
            f'Could not find movies or series for: "{topic}"\n\n'
            "Tips:\n"
            "  - Try the character or franchise name alone: batman, marvel, bond\n"
            "  - Check spelling\n"
            "  - Some very new or obscure titles may not appear on Wikipedia yet"
        )

    confidence = "high" if len(titles) >= 5 else "medium" if len(titles) >= 2 else "low"

    lines = [
        f'Story watch order for: "{topic}"',
        f"Found {len(titles)} movies/series - ordered by STORY (not release date)",
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
        summary = make_story_summary(item.plot) if item.plot else "(Summary not available)"
        lines.append(f"   Summary: {summary}")
        lines.append("")

    lines.append(
        "Note: Order is guessed from plot descriptions and story context on the web. "
        "Different timelines (reboots, alternate universes) may be mixed - "
        "use your judgment for spin-offs and standalone stories."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def build_watch_order(query: str) -> str:
    topic = normalize_topic(query)
    if not topic:
        return "Please type a name - e.g. batman, star wars, harry potter"

    guides = load_guides()
    guide = match_guide(topic, guides)
    if guide:
        return plan_from_guide(topic, guide)

    print(f'  Looking up all "{topic}" movies and series...', file=sys.stderr)
    titles, sources = discover_media(topic)

    if not titles:
        print(f"  No filmography found, searching watch-order guides...", file=sys.stderr)
        return _fallback_watch_order_guides(topic)

    print(f"  Found {len(titles)} titles. Fetching story summaries...", file=sys.stderr)
    sorted_titles = sort_by_story(titles)
    return format_story_plan(topic, sorted_titles, sources)


def _fallback_watch_order_guides(topic: str) -> str:
    """Last resort: parse an existing watch-order list from the web."""
    batches: list[tuple[list[str], int]] = []
    sources: list[str] = []

    for sq in [f"{topic} chronological order", f"{topic} watch order"]:
        for page in search_wikipedia(sq)[:2]:
            text = get_wikipedia_text(page)
            titles = []
            for line in text.split("\n"):
                m = re.match(r"^\s*\d+[.)]\s+(.+)", line.strip())
                if m:
                    titles.append(_clean_title(m.group(1)))
            if len(titles) >= 2:
                batches.append((titles, 3))
                sources.append(f"Wikipedia: {page}")

    if not batches:
        return (
            f'Could not find anything for: "{topic}"\n'
            "Try a single word like: batman, superman, bond, trek"
        )

    # Merge by first appearance
    seen: set[str] = set()
    merged: list[str] = []
    for batch, _ in sorted(batches, key=lambda x: -x[1]):
        for t in batch:
            if t.lower() not in seen:
                seen.add(t.lower())
                merged.append(t)

    lines = [
        f'Story watch order for: "{topic}"',
        "(from existing watch-order guides on the web)",
        "",
    ]
    for i, t in enumerate(merged, 1):
        print(f"  Fetching story summary {i}/{len(merged)}: {t}...", file=sys.stderr)
        lines.append(f"{i}. {t}")
        lines.append(f"   Summary: {_story_line_for_item(t, _release_year_from_title(t))}")
        lines.append("")
    return "\n".join(lines)


def interactive():
    print("=" * 55)
    print("  MOA - Movie Order Agent")
    print("  Type any name. We find the movies/series and order")
    print("  them by STORY with a summary for each title.")
    print("=" * 55)
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
        print(build_watch_order(query))
        print()
        print("-" * 55)
        print()


def main():
    if len(sys.argv) > 1:
        print(build_watch_order(" ".join(sys.argv[1:])))
    else:
        interactive()


if __name__ == "__main__":
    main()
