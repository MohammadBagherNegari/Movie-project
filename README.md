# Movie Story Sorter

A free Python CLI that discovers movies and TV series in a franchise and sorts them by **in-universe story order** (not release date).

Example: *Black Widow* (2021) belongs between *Civil War* and *Infinity War* in story time — this tool puts it there.

## Cost

- **$0** — uses free [TMDb API](https://www.themoviedb.org/settings/api) and Wikipedia
- No paid LLM or local AI required

## Setup

1. **Python 3.11+** installed
2. Create a free TMDb account and request an API key (Developer / Personal use)
3. Install dependencies:

```powershell
cd "c:\Users\hhmsg\Desktop\Cursor\Movie Project"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

4. Copy environment file and add your key:

```powershell
copy .env.example .env
```

Edit `.env`:

```
TMDB_API_KEY=your_actual_key_here
```

## Usage

```powershell
python sort.py "Marvel"
python sort.py "Iron Man"
python sort.py "Star Wars"
python sort.py "Lord of the Rings"
python sort.py "Harry Potter"
```

### Options

| Flag | Description |
|------|-------------|
| `--release-order` | Sort by release date instead of story order |
| `--verbose` / `-v` | Show agent step logging |
| `--no-export` | Skip JSON/TXT export |
| `--output-dir PATH` | Export directory (default: `output/`) |

Exports are written to `output/` as JSON and plain text.

## Web UI

Start the local web server:

```powershell
python run_web.py
```

Then open in your browser:

**http://127.0.0.1:5000**

The page has a centered search bar — type a franchise name (e.g. `Marvel`), hit Search, and results appear in a sorted table with posters, story order, and release year.

Quick-tag buttons (Marvel, Iron Man, Star Wars, Harry Potter) fill the search box and run instantly. Toggle **Sort by release date instead** to compare release vs story order.

The web UI uses the same Python backend as the CLI — no separate setup beyond your TMDb API key in `.env`.

## How it works

```mermaid
flowchart LR
    query[User query] --> discovery[DiscoveryAgent - TMDb]
    discovery --> metadata[MetadataAgent]
    metadata --> chronology[ChronologyAgent - Wikipedia]
    chronology --> matcher[Fuzzy title match]
    matcher --> output[Rich table + export]
```

1. **DiscoveryAgent** — finds all connected movies and series via TMDb (keywords, collections, search)
2. **MetadataAgent** — fetches release dates and details
3. **ChronologyAgent** — loads curated story-order timelines from `data/timelines/`, with Wikipedia as fallback
4. **TitleMatcher** — fuzzy-matches wiki titles to TMDb titles
5. **OutputAgent** — prints a table and exports results

## Supported franchises (built-in)

Configured in [`data/franchise_hints.json`](data/franchise_hints.json):

| Query examples | Franchise |
|----------------|-----------|
| Marvel, MCU | Marvel Cinematic Universe |
| Iron Man | Expands to MCU |
| Star Wars | Star Wars saga |
| Lord of the Rings, LOTR, Hobbit | Middle-earth films |
| Harry Potter, Wizarding World | Wizarding World |

## Adding a new franchise

Edit [`data/franchise_hints.json`](data/franchise_hints.json):

```json
"my franchise": {
  "aliases": ["my franchise", "alternate name"],
  "tmdb_keyword_id": 12345,
  "tmdb_collection_ids": [67890],
  "wiki_timeline_page": "Exact Wikipedia page title",
  "display_name": "My Franchise Display Name"
}
```

- **tmdb_keyword_id** — find on TMDb keyword search (optional)
- **tmdb_collection_ids** — TMDb collection IDs for film series (optional)
- **wiki_timeline_page** — Wikipedia page with a chronological timeline section
- **expand_to_franchise** — use another entry's config (like Iron Man → Marvel)

At least one of `tmdb_keyword_id`, `tmdb_collection_ids`, or search fallback should resolve titles.

## Limitations

- Story order quality depends on public Wikipedia timelines — MCU and Star Wars work well; obscure franchises may fall back to release date
- TMDb does not store narrative chronology; Wikipedia supplements it
- Some TV series and one-shots may not match perfectly due to title differences

## Project structure

```
sort.py              CLI entry point
run_web.py           Web UI server (Flask)
web/                 HTML templates, CSS, JS, API routes
agents/              Discovery, metadata, chronology, output
services/            TMDb and Wikipedia API clients
models/              MediaItem dataclass
data/                Franchise hints + curated story timelines
utils/               Fuzzy title matching
output/              Generated exports (gitignored)
```
