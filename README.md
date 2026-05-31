# 🕷️ Crawl4AI UI

A simple Streamlit GUI for [Crawl4AI](https://github.com/unclecode/crawl4ai) — crawl any
web page, get clean Markdown, preview links and images, capture screenshots, and export
everything as a ZIP or save it straight to a folder on disk.

![Crawl4AI UI](https://img.shields.io/badge/Streamlit-1.54-1E66F5) ![Python](https://img.shields.io/badge/Python-3.10%2B-blue)

## Features

- **Single page or deep crawl** (BFS, configurable depth and page count)
- **Clean Markdown extraction** rendered live in the browser
- **Links tab** — internal vs. external, with counts
- **Assets tab** — preview images with relevance scores
- **Screenshot capture** — full-page PNG
- **Adjustable image-relevance filter** (no upper limit)
- **Export** — pick Markdown / JSON / Images via checkboxes, download as one ZIP
- **Save to disk** — choose a folder (native picker) and write Markdown, metadata,
  screenshots, and downloaded images into a tidy per-page structure
- **Stealth mode** (Patchright) to bypass basic bot detection
- **Caching** to skip re-crawling recent pages

## Install

Requires **Python 3.10+**.

```bash
git clone https://github.com/tsevis/crawl4ai-ui.git
cd crawl4ai-ui

pip install -r requirements.txt

# One-time: download the browser engines Crawl4AI uses
crawl4ai-setup
```

## Run

```bash
streamlit run app.py
```

Then open <http://localhost:8501>.

On macOS you can also double-click **`run.command`** to launch it without using the terminal.

## Usage

1. Paste a URL and click **Crawl**.
2. Browse the result in the **Markdown / Links / Assets / Screenshot / Raw HTML** tabs.
3. To download: tick **Markdown**, **JSON**, and/or **Images**, then click
   **Download selected (.zip)**.
4. To save to disk: enable **Save results to a folder** in the sidebar, pick a folder
   with **Browse…**, and crawl.

### Image score filter

Each image gets a relevance score (≈0–10) based on size, position, and `alt` text.
Set **Min image score** to control what's kept:

| Value | Keeps |
|-------|-------|
| `0`   | Everything (icons, trackers, logos) |
| `3`   | Meaningful images, drops most junk *(good default)* |
| `5+`  | Only high-confidence content images |

> **Note:** Some sites (e.g. Wikimedia) block generic user-agents and return `403` for
> image downloads. The app reports these as failed and continues.

## License

MIT — see [LICENSE](LICENSE). Built on [Crawl4AI](https://github.com/unclecode/crawl4ai)
(Apache-2.0).
