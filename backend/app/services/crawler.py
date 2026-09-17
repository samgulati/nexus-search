from __future__ import annotations

import ipaddress
import re
import socket
import time
from collections import deque
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from ..models import CrawlRequest, CrawlResponse, DocumentIn
from .index_service import index_service

SKIP_EXTENSIONS = {
    ".7z", ".avi", ".css", ".csv", ".doc", ".docx", ".gif", ".gz", ".ico",
    ".jpeg", ".jpg", ".js", ".json", ".map", ".mov", ".mp3", ".mp4", ".pdf",
    ".png", ".ppt", ".pptx", ".rar", ".rss", ".svg", ".tar", ".tgz", ".txt",
    ".webm", ".webp", ".woff", ".woff2", ".xls", ".xlsx", ".xml", ".zip",
}
TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
}

LOCALE_PATH_SEGMENT_RE = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?)(?:/|$)",
    re.IGNORECASE,
)
LOCALE_EXEMPT_SEGMENTS = {
    "en", "en-us", "docs", "api", "learn", "reference", "tutorial",
}

LOW_VALUE_SECTION_NAMES = {
    "navigation",
    "table of contents",
    "feedback",
    "community",
    "training",
    "certifications",
}

LOW_VALUE_TEXT_MARKERS = (
    "theme auto light dark",
    "index modules | next | previous",
)


def is_probable_localized_path(url: str) -> bool:
    path = urlparse(url).path or "/"
    first = path.strip("/").split("/", 1)[0].lower()
    if first in LOCALE_EXEMPT_SEGMENTS:
        return False
    return bool(LOCALE_PATH_SEGMENT_RE.match(path))


def is_low_value_block(section: str, text: str) -> bool:
    section_norm = " ".join((section or "").lower().split()).strip("¶ ")
    if section_norm in LOW_VALUE_SECTION_NAMES:
        return True
    text_norm = " ".join((text or "").lower().split())
    if any(marker in text_norm for marker in LOW_VALUE_TEXT_MARKERS):
        return True
    if len(text_norm) < 260 and text.count("|") >= 3:
        return True
    return False


def _safe_public_host(hostname: str) -> bool:
    if hostname in {"localhost", "localhost.localdomain"}:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except Exception:
        return False
    return True


def canonicalize_url(url: str) -> str:
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
        return ""

    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMS:
            continue
        query.append((key, value))

    normalized_path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        normalized_path,
        "",
        urlencode(query, doseq=True),
        "",
    ))


def chunk_text(text: str, *, target_chars: int = 1800, overlap_chars: int = 220) -> list[str]:
    clean = " ".join(text.split())
    if len(clean) < 120:
        return []
    if len(clean) <= target_chars:
        return [clean]

    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and current_len + len(sentence) + 1 > target_chars:
            chunk = " ".join(current).strip()
            if len(chunk) >= 120:
                chunks.append(chunk)

            tail = chunk[-overlap_chars:]
            first_space = tail.find(" ")
            if first_space >= 0:
                tail = tail[first_space + 1:]
            current = [tail] if tail else []
            current_len = len(tail)

        current.append(sentence)
        current_len += len(sentence) + 1

    final = " ".join(current).strip()
    if len(final) >= 120:
        chunks.append(final)

    # Avoid indexing a pathological number of chunks from huge navigation-heavy pages.
    return chunks[:24]


def extract_page_documents(html: str, url: str, default_title: str) -> list[DocumentIn]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "form", "button"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.body or soup
    for tag in main.find_all(["aside"]):
        tag.decompose()

    page_title = default_title
    if soup.title and soup.title.string:
        page_title = soup.title.string.strip()[:300] or default_title

    # Preserve section boundaries where documentation pages expose headings.
    blocks: list[tuple[str, str]] = []
    heading = page_title
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        text = " ".join(buffer).strip()
        if len(text) >= 120:
            blocks.append((heading, text))
        buffer = []

    for node in main.find_all(["h1", "h2", "h3", "p", "pre", "li"], recursive=True):
        if node.name in {"h1", "h2", "h3"}:
            flush()
            candidate = " ".join(node.get_text(" ", strip=True).split())
            if candidate:
                heading = candidate[:220]
        else:
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                buffer.append(text)
    flush()

    if not blocks:
        raw = " ".join(main.get_text(" ", strip=True).split())
        blocks = [(page_title, raw)]

    docs: list[DocumentIn] = []
    seen_text: set[str] = set()
    chunk_index = 0
    for section, text in blocks:
        if is_low_value_block(section, text):
            continue
        for chunk in chunk_text(text):
            if is_low_value_block(section, chunk):
                continue
            normalized = " ".join(chunk.lower().split())
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
            chunk_index += 1
            title = page_title if section == page_title else f"{page_title} — {section}"
            docs.append(DocumentIn(
                title=title[:300],
                text=chunk,
                url=url,
                source=f"trusted-docs:{urlparse(url).netloc}",
            ))
    return docs


class Crawler:
    def __init__(self) -> None:
        self._robots: dict[str, RobotFileParser] = {}

    async def _allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            rp.set_url(f"{origin}/robots.txt")
            try:
                response = await client.get(rp.url, timeout=5)
                if response.status_code < 400:
                    rp.parse(response.text.splitlines())
                else:
                    rp.parse([])
            except Exception:
                rp.parse([])
            self._robots[origin] = rp
        return self._robots[origin].can_fetch(settings.crawl_user_agent, url)

    async def crawl(
        self,
        request: CrawlRequest,
        index_many: Callable[[list[DocumentIn]], Awaitable[tuple[int, int]]] | None = None,
        refresh_many: Callable[
            [list[DocumentIn]],
            Awaitable[tuple[int, int, int, int, int]],
        ] | None = None,
    ) -> CrawlResponse:
        t0 = time.perf_counter()
        queue = deque((canonicalize_url(str(seed)), 0) for seed in request.seeds)
        seen: set[str] = set()
        items: list[DocumentIn] = []
        skipped = 0
        failed = 0
        fetched = 0
        html_pages = 0
        content_pages = 0
        empty_pages = 0
        allowed_domains = {urlparse(str(seed)).netloc.lower() for seed in request.seeds}

        headers = {"User-Agent": settings.crawl_user_agent}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=12) as client:
            while queue and len(seen) < request.max_pages:
                url, depth = queue.popleft()
                if not url or url in seen:
                    continue
                seen.add(url)
                parsed = urlparse(url)

                if is_probable_localized_path(url):
                    skipped += 1
                    continue
                if not parsed.hostname or not _safe_public_host(parsed.hostname):
                    skipped += 1
                    continue
                if request.same_domain_only and parsed.netloc.lower() not in allowed_domains:
                    skipped += 1
                    continue

                try:
                    if not await self._allowed(client, url):
                        skipped += 1
                        continue

                    response = await client.get(url)
                    fetched += 1
                    final_url = canonicalize_url(str(response.url))
                    content_type = response.headers.get("content-type", "").lower()
                    if response.status_code >= 400 or "text/html" not in content_type or not final_url:
                        skipped += 1
                        continue
                    html_pages += 1

                    final_host = urlparse(final_url).hostname
                    if not final_host or not _safe_public_host(final_host):
                        skipped += 1
                        continue
                    if request.same_domain_only and urlparse(final_url).netloc.lower() not in allowed_domains:
                        skipped += 1
                        continue

                    default_title = parsed.path.strip("/") or parsed.netloc
                    page_docs = extract_page_documents(response.text, final_url, default_title)
                    if page_docs:
                        content_pages += 1
                        items.extend(page_docs)
                    else:
                        empty_pages += 1

                    if depth < request.max_depth:
                        soup = BeautifulSoup(response.text, "html.parser")
                        links_added = 0
                        for anchor in soup.find_all("a", href=True):
                            candidate = canonicalize_url(urljoin(final_url, anchor["href"]))
                            if not candidate or candidate in seen:
                                continue
                            if is_probable_localized_path(candidate):
                                continue
                            cparsed = urlparse(candidate)
                            if request.same_domain_only and cparsed.netloc.lower() not in allowed_domains:
                                continue
                            queue.append((candidate, depth + 1))
                            links_added += 1
                            if links_added >= 250:
                                break
                except Exception:
                    failed += 1

        stale_chunks_removed = 0
        refreshed_urls = 0
        unchanged_urls = 0

        if request.refresh_existing:
            # Only non-empty, successfully extracted pages appear in `items`.
            # That makes refresh fail-safe: a fetch/extraction failure never
            # deletes a previously indexed page.
            if refresh_many is None:
                pages: dict[str, list[DocumentIn]] = {}
                for item in items:
                    if item.url:
                        pages.setdefault(item.url, []).append(item)
                indexed = duplicates = 0
                for url, documents in pages.items():
                    added, dupes, removed, unchanged = index_service.replace_url(url, documents)
                    indexed += added
                    duplicates += dupes
                    stale_chunks_removed += removed
                    if unchanged:
                        unchanged_urls += 1
                    else:
                        refreshed_urls += 1
            else:
                (
                    indexed,
                    duplicates,
                    stale_chunks_removed,
                    refreshed_urls,
                    unchanged_urls,
                ) = await refresh_many(items)
        elif index_many is None:
            indexed, duplicates = index_service.add_many(items)
        else:
            indexed, duplicates = await index_many(items)

        return CrawlResponse(
            crawled=len(seen),
            indexed=indexed,
            skipped=skipped + duplicates,
            failed=failed,
            took_ms=round((time.perf_counter() - t0) * 1000, 2),
            fetched=fetched,
            html_pages=html_pages,
            content_pages=content_pages,
            empty_pages=empty_pages,
            chunks_extracted=len(items),
            refreshed_urls=refreshed_urls,
            unchanged_urls=unchanged_urls,
            stale_chunks_removed=stale_chunks_removed,
        )


crawler = Crawler()
