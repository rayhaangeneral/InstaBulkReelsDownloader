# InstaDownloader (local web GUI)

This repo downloads Instagram reels/posts using [Instaloader](https://instaloader.github.io/) and includes a **local web interface** (Flask) so you can paste links in your browser and download the resulting `.mp4`.

## Requirements

- Python 3.10+ (you have Python installed already)
- Windows PowerShell is fine

## Setup

From the project folder:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If you want the “collect all reels for username” feature to work reliably, install the browser runtime once:

```bash
python -m playwright install chromium
```

## Run the web GUI

```bash
python webapp.py
```

Then open `http://127.0.0.1:5007` in your browser.

## New: collect reel links from a username

In the web UI, use **“Collect reel links from username”**.

- It produces a downloadable `.txt` file under `reels/exports/…`
- It also shows a textbox with all links + a **Copy to clipboard** button

This replaces the “scroll + DevTools console script” approach.

### If collection fails but single downloads work

Instagram sometimes blocks **profile pagination (GraphQL)** even when downloading a specific reel (by shortcode) works.

The app now tries:

- `instaloader_graphql`: full collection (best case)
- `html_fallback`: best-effort scrape from the profile page (usually **only first page** of reels)

The job page shows which **Method** was used.

### Using cookies (recommended)

- Put your browser-exported cookies in `cookie.json` (same folder as the scripts).
- The app will load them automatically if the file exists.

Important: `cookie.json` contains session credentials. **Do not share it** or commit it to a public repo.

## Run the original script (batch file input)

This keeps the original “download many URLs from a text file” flow:

1. Put URLs (one per line) in `glitch_mindset_reels.txt`
2. Run:

```bash
python 1.py
```

Downloads go into the `reels/` folder.

## Files

- `webapp.py`: local web UI (paste URLs, see results, download files)
- `insta_downloader.py`: shared download logic (cookies, shortcode parsing, downloads)
- `1.py`: original batch script (now uses the shared module)

## Notes / troubleshooting

- **Rate limits (429)**: set a delay in the UI (e.g. 10–30 seconds) and try again later.
- **Auth errors (401)**: your `cookie.json` may be missing/expired.
- **Private accounts**: you typically need valid cookies for the account that can view the post.

