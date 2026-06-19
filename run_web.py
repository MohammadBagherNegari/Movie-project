#!/usr/bin/env python3
"""Start the Movie Story Sorter web UI."""

import webbrowser

from services.config import ensure_env_file
from web.app import WEB_URL, main

if __name__ == "__main__":
    ensure_env_file()
    print(f"\n  Open in your browser: {WEB_URL}\n")
    webbrowser.open(WEB_URL)
    main()
