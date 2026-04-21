import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple


try:
    import instaloader
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "Instaloader is required. Install with: pip install instaloader"
    ) from e


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)


@dataclass(frozen=True)
class DownloadResult:
    url: str
    shortcode: Optional[str]
    ok: bool
    error: Optional[str]
    files: Tuple[str, ...]


def extract_shortcode(url: str) -> Optional[str]:
    u = (url or "").strip()
    if not u:
        return None

    for marker in ("/reel/", "/p/", "/tv/"):
        if marker in u:
            return u.split(marker, 1)[1].split("/", 1)[0].strip() or None
    return None


def build_loader(
    *,
    output_folder: str,
    user_agent: str = DEFAULT_USER_AGENT,
    cookie_file: Optional[str] = "cookie.json",
    max_connection_attempts: int = 3,
    request_timeout: int = 30,
) -> "instaloader.Instaloader":
    os.makedirs(output_folder, exist_ok=True)

    L = instaloader.Instaloader(
        dirname_pattern=output_folder,
        filename_pattern="{shortcode}",
        download_comments=False,
        download_video_thumbnails=False,
        download_geotags=False,
        save_metadata=False,
        compress_json=False,
        user_agent=user_agent,
    )

    # Avoid hanging forever on retries (esp. GraphQL 403 loops)
    try:
        L.context.max_connection_attempts = max(1, int(max_connection_attempts))
    except Exception:
        pass
    try:
        L.context.request_timeout = max(1, int(request_timeout))
    except Exception:
        pass

    if cookie_file:
        _load_cookies_from_json(L, cookie_file)

    return L


def _load_cookies_from_json(L: "instaloader.Instaloader", cookie_file: str) -> int:
    if not os.path.exists(cookie_file):
        return 0

    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies_data = json.load(f)

    count = 0
    for cookie in cookies_data:
        if isinstance(cookie, dict) and "name" in cookie and "value" in cookie:
            L.context._session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain", ".instagram.com"),
                path=cookie.get("path", "/"),
            )
            count += 1

    # Some Instagram endpoints are picky; set a few common headers if we can.
    try:
        csrf = L.context._session.cookies.get("csrftoken")
        if csrf:
            L.context._session.headers["X-CSRFToken"] = csrf
        L.context._session.headers.setdefault("Referer", "https://www.instagram.com/")
        L.context._session.headers.setdefault("Origin", "https://www.instagram.com")
    except Exception:
        # If instaloader internals change, cookies still help even without these headers.
        pass

    return count


def _find_downloaded_files(output_folder: str, shortcode: str) -> List[str]:
    if not os.path.isdir(output_folder):
        return []

    hits: List[str] = []
    for name in os.listdir(output_folder):
        if name.startswith(shortcode):
            hits.append(os.path.join(output_folder, name))

    hits.sort(key=lambda p: (os.path.splitext(p)[1].lower() != ".mp4", p.lower()))
    return hits


def download_one(
    L: "instaloader.Instaloader",
    *,
    url: str,
    output_folder: str,
    cooldown_on_401_429_s: int = 120,
) -> DownloadResult:
    shortcode = extract_shortcode(url)
    if not shortcode:
        return DownloadResult(
            url=url,
            shortcode=None,
            ok=False,
            error="Invalid URL (expected /reel/, /p/, or /tv/).",
            files=(),
        )

    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=output_folder)
        files = tuple(_find_downloaded_files(output_folder, shortcode))
        return DownloadResult(url=url, shortcode=shortcode, ok=True, error=None, files=files)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "429" in msg:
            time.sleep(cooldown_on_401_429_s)
        return DownloadResult(url=url, shortcode=shortcode, ok=False, error=msg, files=())


def download_many(
    L: "instaloader.Instaloader",
    *,
    urls: Iterable[str],
    output_folder: str,
    sleep_between_s: Tuple[int, int] = (0, 0),
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[DownloadResult]:
    results: List[DownloadResult] = []
    min_s, max_s = sleep_between_s
    url_list = [u for u in urls if (u or "").strip()]

    for idx, url in enumerate(url_list):
        if should_stop and should_stop():
            break
        results.append(download_one(L, url=url, output_folder=output_folder))

        if idx < len(url_list) - 1 and max_s > 0:
            if min_s >= max_s:
                delay = min_s
            else:
                import random

                delay = random.randint(min_s, max_s)
            # Sleep in small chunks so stop requests are respected quickly
            remaining = max(0, int(delay))
            while remaining > 0:
                if should_stop and should_stop():
                    return results
                step = min(1, remaining)
                time.sleep(step)
                remaining -= step

    return results


def post_to_instagram_url(post: "instaloader.Post") -> str:
    shortcode = getattr(post, "shortcode", None) or ""
    product_type = getattr(post, "product_type", None)
    if product_type == "clips":
        return f"https://www.instagram.com/reel/{shortcode}/"
    return f"https://www.instagram.com/p/{shortcode}/"


def collect_post_links_for_username(
    L: "instaloader.Instaloader",
    *,
    username: str,
    limit: int = 500,
    reels_only: bool = True,
) -> List[str]:
    username = (username or "").strip().lstrip("@")
    if not username:
        return []

    if limit <= 0:
        return []

    profile = instaloader.Profile.from_username(L.context, username)

    out: List[str] = []
    try:
        iterator = profile.get_posts()
        for post in iterator:
            product_type = getattr(post, "product_type", None)
            is_reel = product_type == "clips"
            if reels_only and not is_reel:
                continue

            out.append(post_to_instagram_url(post))
            if len(out) >= limit:
                break
    except KeyError as e:
        # Instaloader sometimes surfaces blocked/changed JSON as KeyError('edges').
        if str(e).strip("'\"") == "edges":
            raise RuntimeError(
                "Instagram blocked the request (often 403 to graphql/query). "
                "Try refreshing `cookie.json` (new export), wait a bit, and retry."
            ) from e
        raise
    except Exception as e:
        msg = str(e)
        if "403" in msg or "Forbidden" in msg:
            raise RuntimeError(
                "Got 403 Forbidden from Instagram. Your cookies may be expired or Instagram is rate-limiting/blocking. "
                "Try exporting a fresh `cookie.json` (while logged in), wait, then retry."
            ) from e
        raise

    # de-dupe while preserving order
    seen = set()
    deduped: List[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            deduped.append(u)

    return deduped


def _dedupe_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _scrape_reel_links_from_html(L: "instaloader.Instaloader", username: str) -> List[str]:
    """
    Best-effort fallback when GraphQL pagination is blocked.
    This usually returns only the first page worth of reels/posts.
    """
    username = (username or "").strip().lstrip("@")
    if not username:
        return []

    session = L.context._session
    urls_to_try = [
        f"https://www.instagram.com/{username}/reels/",
        f"https://www.instagram.com/{username}/",
    ]

    all_links: List[str] = []
    for page_url in urls_to_try:
        r = session.get(page_url, timeout=30)
        if r.status_code == 403:
            # If the HTML page is blocked too, there's nothing else we can do without a browser.
            raise RuntimeError(
                "Got 403 Forbidden while loading the profile page. "
                "Try exporting a fresh `cookie.json` and retry later."
            )
        if r.status_code >= 400:
            continue

        html = r.text or ""
        shortcodes = re.findall(r"/reel/([A-Za-z0-9_-]+)/", html)
        all_links.extend([f"https://www.instagram.com/reel/{sc}/" for sc in shortcodes])

        # Sometimes reel links appear URL-encoded; capture those too
        shortcodes2 = re.findall(r"%2Freel%2F([A-Za-z0-9_-]+)%2F", html)
        all_links.extend([f"https://www.instagram.com/reel/{sc}/" for sc in shortcodes2])

    return _dedupe_keep_order(all_links)


def collect_reel_links_for_username(
    L: "instaloader.Instaloader",
    *,
    username: str,
    limit: int = 500,
) -> Tuple[List[str], str]:
    """
    Collect reel links for a username.

    Returns (links, method) where method is one of:
    - "instaloader_graphql" (full pagination when allowed)
    - "html_fallback" (best-effort first page scrape)
    """
    try:
        links = collect_post_links_for_username(L, username=username, limit=limit, reels_only=True)
        return links, "instaloader_graphql"
    except Exception:
        links = _scrape_reel_links_from_html(L, username=username)
        if limit > 0:
            links = links[:limit]
        return links, "html_fallback"


def _load_cookie_json_for_playwright(cookie_file: str) -> List[dict]:
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies_data = json.load(f)

    def map_samesite(v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v2 = str(v).lower()
        if v2 in ("no_restriction", "none"):
            return "None"
        if v2 == "lax":
            return "Lax"
        if v2 == "strict":
            return "Strict"
        return None

    out: List[dict] = []
    for c in cookies_data:
        if not isinstance(c, dict):
            continue
        if "name" not in c or "value" not in c:
            continue

        item = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".instagram.com"),
            "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", True)),
        }
        if "expirationDate" in c and c["expirationDate"] is not None:
            try:
                item["expires"] = float(c["expirationDate"])
            except Exception:
                pass
        ss = map_samesite(c.get("sameSite"))
        if ss:
            item["sameSite"] = ss
        out.append(item)
    return out


def collect_reel_links_playwright(
    *,
    username: str,
    cookie_file: str,
    limit: int = 1500,
    max_scrolls: int = 220,
    pause_every: int = 10,
    pause_s: int = 15,
    tick_s: float = 3.0,
    headless: bool = True,
    user_agent: str = DEFAULT_USER_AGENT,
    on_progress: Optional[Callable[[dict], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[str]:
    """
    Browser automation collector (closest to the DevTools script approach).

    Requires:
      pip install playwright
      python -m playwright install chromium
    """
    username = (username or "").strip().lstrip("@")
    if not username:
        return []

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Playwright is not installed. Install with: pip install playwright "
            "then run: python -m playwright install chromium"
        ) from e

    cookies = _load_cookie_json_for_playwright(cookie_file)
    target_url = f"https://www.instagram.com/{username}/reels/"

    found: List[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=user_agent, viewport={"width": 1280, "height": 720})
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()
        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)

        def progress(event: dict) -> None:
            if on_progress:
                try:
                    on_progress(event)
                except Exception:
                    pass

        # Fail fast if we got redirected to login/checkpoint
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        if any(x in current_url for x in ("/accounts/login", "/challenge/", "/checkpoint/")):
            raise RuntimeError(
                f"Not logged in (redirected to {current_url}). Your `cookie.json` may not be valid for browser automation."
            )

        stuck = 0
        last_height = 0

        for i in range(max_scrolls):
            if should_stop and should_stop():
                progress(
                    {
                        "scroll": i,
                        "max_scrolls": max_scrolls,
                        "links": len(found),
                        "url": current_url,
                        "stuck": stuck,
                        "note": "stop requested",
                    }
                )
                break
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""

            # collect
            hrefs = page.eval_on_selector_all(
                'a[href*="/reel/"]',
                "els => els.map(e => e.href)",
            )
            if hrefs:
                found.extend([h for h in hrefs if isinstance(h, str)])
                found = _dedupe_keep_order(found)
            progress(
                {
                    "scroll": i + 1,
                    "max_scrolls": max_scrolls,
                    "links": len(found),
                    "url": current_url,
                    "stuck": stuck,
                }
            )
            if limit and len(found) >= limit:
                break

            # height / stuck detection
            height = page.evaluate("() => document.body.scrollHeight")
            if height == last_height:
                stuck += 1
            else:
                stuck = 0
                last_height = height

            if stuck >= 20:
                break

            # scroll logic: up a bit then to bottom
            page.evaluate("() => window.scrollBy(0, -200)")
            page.wait_for_timeout(100)
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")

            if pause_every and (i + 1) % pause_every == 0:
                # Allow stop during pauses
                total_ms = int(pause_s * 1000)
                elapsed = 0
                while elapsed < total_ms:
                    if should_stop and should_stop():
                        break
                    page.wait_for_timeout(min(250, total_ms - elapsed))
                    elapsed += 250
            else:
                total_ms = int(tick_s * 1000)
                elapsed = 0
                while elapsed < total_ms:
                    if should_stop and should_stop():
                        break
                    page.wait_for_timeout(min(250, total_ms - elapsed))
                    elapsed += 250

        context.close()
        browser.close()

    if limit:
        return found[:limit]
    return found

