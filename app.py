import asyncio
import base64
import io
import json
import re
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import streamlit as st
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

st.set_page_config(page_title="Crawl4AI", page_icon="🕷️", layout="wide")

st.title("🕷️ Crawl4AI")
st.caption("Web crawler with clean Markdown extraction")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def slug(url: str) -> str:
    parsed = urlparse(url)
    raw = parsed.netloc + parsed.path
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw).strip("_")[:80] or "page"


def pick_folder_dialog() -> str | None:
    """Native macOS folder picker via AppleScript (safe inside Streamlit's thread)."""
    script = 'POSIX path of (choose folder with prompt "Select output folder")'
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        path = out.stdout.strip()
        return path.rstrip("/") if path else None
    except Exception:
        return None


def fetch_image(base_url: str, src: str) -> tuple[bytes, str] | None:
    """Resolve a (possibly protocol-/root-relative) image URL and download it.

    Returns (bytes, extension) or None on failure.
    """
    if not src:
        return None

    # Inline data URI
    if src.startswith("data:"):
        try:
            header, b64 = src.split(",", 1)
            ext = "png"
            m = re.search(r"image/(\w+)", header)
            if m:
                ext = m.group(1)
            return base64.b64decode(b64), ext
        except Exception:
            return None

    full = urljoin(base_url, src)  # fixes //host/... and /path/... and relative
    cache = st.session_state.setdefault("img_cache", {})
    if full in cache:
        return cache[full]

    # Send a Referer matching the page origin so hotlink-protected images
    # (which 403 without it) download correctly.
    parsed = urlparse(base_url)
    headers = {**UA,
               "Referer": base_url,
               "Origin": f"{parsed.scheme}://{parsed.netloc}",
               "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8"}

    result = None
    try:
        with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
            resp = client.get(full)
            if resp.status_code == 200 and resp.content:
                ext = Path(urlparse(full).path).suffix.lstrip(".").lower()
                ctype = resp.headers.get("content-type", "")
                if not ext and "image/" in ctype:
                    ext = ctype.split("image/")[1].split(";")[0]
                result = (resp.content, ext or "jpg")
    except Exception:
        result = None

    cache[full] = result
    return result


def filtered_images(result, min_score: int) -> list:
    return [
        img for img in (result.media or {}).get("images", [])
        if img.get("src") and img.get("score", 0) >= min_score
    ]


def build_zip(results, timestamp, inc_md, inc_json, inc_images, min_score) -> tuple[bytes, int, int]:
    buf = io.BytesIO()
    img_ok = img_fail = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in results:
            name = slug(r.url)
            if inc_md:
                z.writestr(f"{name}/{name}.md", f"# {r.url}\n\n{r.markdown or ''}")
            if inc_json:
                meta = {"url": r.url, "crawled_at": timestamp, "links": r.links}
                z.writestr(f"{name}/meta.json",
                           json.dumps(meta, indent=2, ensure_ascii=False))
            if r.screenshot:
                z.writestr(f"{name}/screenshot.png", base64.b64decode(r.screenshot))
            if inc_images:
                for i, img in enumerate(filtered_images(r, min_score)):
                    got = fetch_image(r.url, img["src"])
                    if got:
                        data, ext = got
                        z.writestr(f"{name}/assets/images/img_{i:04d}.{ext}", data)
                        img_ok += 1
                    else:
                        img_fail += 1
    return buf.getvalue(), img_ok, img_fail


def save_to_disk(results, folder, inc_images, min_score, timestamp) -> tuple[list, int, int]:
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    saved, img_ok, img_fail = [], 0, 0

    for r in results:
        name = slug(r.url)
        page_dir = root / name
        page_dir.mkdir(exist_ok=True)

        md_path = page_dir / f"{name}.md"
        md_path.write_text(f"# {r.url}\n\n{r.markdown or ''}", encoding="utf-8")
        saved.append(str(md_path))

        meta = {"url": r.url, "crawled_at": timestamp, "links": r.links}
        (page_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        if r.screenshot:
            (page_dir / "screenshot.png").write_bytes(base64.b64decode(r.screenshot))

        if inc_images:
            imgs = filtered_images(r, min_score)
            if imgs:
                img_dir = page_dir / "assets" / "images"
                img_dir.mkdir(parents=True, exist_ok=True)
                for i, img in enumerate(imgs):
                    got = fetch_image(r.url, img["src"])
                    if got:
                        data, ext = got
                        (img_dir / f"img_{i:04d}.{ext}").write_bytes(data)
                        img_ok += 1
                    else:
                        img_fail += 1
    return saved, img_ok, img_fail


# ── Crawl ─────────────────────────────────────────────────────────────────────
async def _crawl(url, mode, max_pages, max_depth, headless, stealth, use_cache,
                 screenshot, scan_lazy):
    browser_cfg = BrowserConfig(headless=headless, use_managed_browser=stealth)
    cache_mode = CacheMode.ENABLED if use_cache else CacheMode.BYPASS
    # Scroll the whole page and wait for images so lazy-loaded media is captured.
    lazy_opts = dict(
        scan_full_page=True, scroll_delay=0.4,
        wait_for_images=True, delay_before_return_html=1.0,
    ) if scan_lazy else {}
    if mode == "Deep crawl":
        run_cfg = CrawlerRunConfig(
            cache_mode=cache_mode, screenshot=screenshot,
            deep_crawl_strategy=BFSDeepCrawlStrategy(max_depth=max_depth, max_pages=max_pages),
            **lazy_opts,
        )
    else:
        run_cfg = CrawlerRunConfig(cache_mode=cache_mode, screenshot=screenshot, **lazy_opts)
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        results = await crawler.arun(url=url, config=run_cfg)
        return results if isinstance(results, list) else [results]


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Options")

    mode = st.radio("Crawl mode", ["Single page", "Deep crawl"], horizontal=True)
    if mode == "Deep crawl":
        max_pages = st.slider("Max pages", 2, 50, 10)
        max_depth = st.slider("Max depth", 1, 5, 2)
    else:
        max_pages, max_depth = 1, 1

    st.divider()
    st.subheader("Browser")
    headless = st.toggle("Headless", value=True)
    stealth = st.toggle("Stealth mode", value=False, help="Slower but bypasses bot detection")
    screenshot = st.toggle("Screenshot", value=False, help="Capture full-page screenshot")
    scan_lazy = st.toggle(
        "Capture lazy-loaded images", value=True,
        help="Scrolls the full page and waits for images to load. "
             "Needed for most modern sites that lazy-load images — leave on if "
             "images aren't being found. Slightly slower.")

    st.divider()
    st.subheader("Cache")
    use_cache = st.toggle("Use cache", value=True, help="Skip re-crawling recent pages")

    st.divider()
    st.subheader("Image filter")
    min_image_score = st.number_input(
        "Min image score",
        min_value=0, value=0, step=1,
        help="Only images with a relevance score ≥ this value are kept. "
             "No upper limit — set it as high as you like. 0 = keep every image.",
    )

    st.divider()
    st.subheader("Save to disk")
    save_enabled = st.toggle("Save results to a folder", value=False)
    if "save_folder" not in st.session_state:
        st.session_state.save_folder = str(Path.home() / "crawl4ai-output")
    if save_enabled:
        c1, c2 = st.columns([3, 1])
        with c1:
            st.session_state.save_folder = st.text_input(
                "Output folder", value=st.session_state.save_folder,
                label_visibility="collapsed",
            )
        with c2:
            if st.button("Browse…", use_container_width=True):
                chosen = pick_folder_dialog()
                if chosen:
                    st.session_state.save_folder = chosen
                    st.rerun()
        save_images = st.toggle("Also download images", value=True)
    else:
        save_images = False


# ── Input ─────────────────────────────────────────────────────────────────────
url = st.text_input("URL", placeholder="https://example.com", label_visibility="collapsed")
crawl_btn = st.button("Crawl", type="primary", use_container_width=True)

if crawl_btn:
    if not url:
        st.warning("Please enter a URL.")
    else:
        with st.spinner(f"Crawling {url} …"):
            try:
                results = asyncio.run(_crawl(
                    url, mode, max_pages, max_depth,
                    headless, stealth, use_cache, screenshot, scan_lazy))
            except Exception as e:
                st.error(f"Crawl failed: {e}")
                st.stop()
        successful = [r for r in results if r.success]
        st.session_state["crawl"] = {
            "results": successful,
            "failed": [r for r in results if not r.success],
            "ts": datetime.now().strftime("%Y%m%d_%H%M%S"),
        }
        st.session_state["img_cache"] = {}  # fresh cache per crawl

        # Save to disk immediately (so disk output reflects this crawl)
        if save_enabled and st.session_state.save_folder and successful:
            with st.spinner("Saving files to disk …"):
                try:
                    saved, ok, fail = save_to_disk(
                        successful, st.session_state.save_folder,
                        save_images, min_image_score,
                        st.session_state["crawl"]["ts"])
                    st.session_state["crawl"]["disk"] = (
                        st.session_state.save_folder, len(saved), ok, fail)
                except Exception as e:
                    st.error(f"Save failed: {e}")


# ── Results (read from session_state so downloads don't wipe the page) ─────────
data = st.session_state.get("crawl")
if data:
    successful = data["results"]
    failed = data["failed"]
    timestamp = data["ts"]

    st.success(f"{len(successful)} page(s) crawled"
               + (f", {len(failed)} failed" if failed else ""))

    if "disk" in data:
        folder, n_files, ok, fail = data["disk"]
        msg = f"Saved {n_files} file(s) to `{folder}`"
        if save_enabled:
            msg += f" · images: {ok} downloaded"
            if fail:
                msg += f", {fail} failed"
        st.info(msg)

    if not successful:
        st.stop()

    # ── Download selection (checkboxes + single button) ───────────────────────
    st.subheader("Download")
    d1, d2, d3, d4 = st.columns([1, 1, 1, 2])
    with d1:
        inc_md = st.checkbox("Markdown", value=True)
    with d2:
        inc_json = st.checkbox("JSON", value=True)
    with d3:
        inc_images = st.checkbox("Images", value=True)
    with d4:
        if not (inc_md or inc_json or inc_images):
            st.caption("Select at least one item to download.")
            zip_bytes = b""
        else:
            with st.spinner("Preparing ZIP…"):
                zip_bytes, img_ok, img_fail = build_zip(
                    successful, timestamp,
                    inc_md, inc_json, inc_images, min_image_score)
            st.download_button(
                "⬇ Download selected (.zip)",
                zip_bytes,
                file_name=f"crawl4ai_{timestamp}.zip",
                mime="application/zip",
                use_container_width=True,
                disabled=not zip_bytes,
            )
            if inc_images:
                if img_ok:
                    st.caption(f"📷 {img_ok} image(s) packed"
                               + (f", {img_fail} failed to download" if img_fail else ""))
                elif img_fail:
                    st.caption(f"⚠️ 0 images packed — all {img_fail} failed "
                               "(site may block hotlinking or require login).")
                else:
                    st.caption("No images matched the filter on this page. "
                               "Try lowering 'Min image score' to 0.")

    st.divider()

    # ── Per-page viewer ───────────────────────────────────────────────────────
    if len(successful) > 1:
        idx = st.selectbox("Page", options=range(len(successful)),
                           format_func=lambda i: successful[i].url)
        pages = [successful[idx]]
    else:
        pages = successful

    for result in pages:
        tab_md, tab_links, tab_assets, tab_ss, tab_html = st.tabs(
            ["Markdown", "Links", "Assets", "Screenshot", "Raw HTML"])

        with tab_md:
            st.markdown(result.markdown or "_No content extracted._")

        with tab_links:
            links = result.links or {}
            internal, external = links.get("internal", []), links.get("external", [])
            c1, c2 = st.columns(2)
            for col, label, items in ((c1, "Internal", internal), (c2, "External", external)):
                with col:
                    st.metric(label, len(items))
                    for lnk in items[:100]:
                        href = lnk.get("href", "")
                        text = lnk.get("text", href) or href
                        st.markdown(f"- [{text}]({href})")
                    if len(items) > 100:
                        st.caption(f"… and {len(items) - 100} more")

        with tab_assets:
            imgs = filtered_images(result, min_image_score)
            all_imgs = (result.media or {}).get("images", [])
            st.metric("Images (after filter)", f"{len(imgs)} / {len(all_imgs)}")
            if imgs:
                cols = st.columns(4)
                for i, img in enumerate(imgs[:40]):
                    with cols[i % 4]:
                        src = urljoin(result.url, img.get("src", ""))
                        try:
                            st.image(src, caption=f"score:{img.get('score','?')}")
                        except Exception:
                            st.caption(src)
                if len(imgs) > 40:
                    st.caption(f"… and {len(imgs) - 40} more")
            else:
                st.info("No images pass the current filter.")

        with tab_ss:
            if result.screenshot:
                st.image(base64.b64decode(result.screenshot),
                         caption=result.url, use_container_width=True)
            else:
                st.info("Enable the Screenshot toggle in the sidebar before crawling.")

        with tab_html:
            st.code(result.html or "", language="html")

    if failed:
        with st.expander(f"Failed pages ({len(failed)})"):
            for r in failed:
                st.error(f"{r.url}: {r.error_message}")
