from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from ..models import CrawlRequest, CrawlResponse, DocumentIn
from .index_service import index_service


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

    async def crawl(self, request: CrawlRequest) -> CrawlResponse:
        t0 = time.perf_counter()
        queue = deque((str(seed), 0) for seed in request.seeds)
        seen: set[str] = set()
        items: list[DocumentIn] = []
        skipped = 0
        failed = 0
        allowed_domains = {urlparse(str(seed)).netloc for seed in request.seeds}

        headers = {"User-Agent": settings.crawl_user_agent}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10) as client:
            while queue and len(seen) < request.max_pages:
                url, depth = queue.popleft()
                url, _fragment = urldefrag(url)
                if url in seen:
                    continue
                seen.add(url)
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or not _safe_public_host(parsed.hostname):
                    skipped += 1
                    continue
                if request.same_domain_only and parsed.netloc not in allowed_domains:
                    skipped += 1
                    continue
                try:
                    if not await self._allowed(client, url):
                        skipped += 1
                        continue
                    response = await client.get(url)
                    if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", ""):
                        skipped += 1
                        continue
                    soup = BeautifulSoup(response.text, "html.parser")
                    for tag in soup(["script", "style", "noscript", "svg"]):
                        tag.decompose()
                    title = (soup.title.string.strip() if soup.title and soup.title.string else parsed.path or parsed.netloc)[:300]
                    text = " ".join(soup.get_text(" ", strip=True).split())
                    if len(text) >= 120:
                        items.append(DocumentIn(title=title, text=text[:50000], url=str(response.url), source="crawler"))
                    if depth < request.max_depth:
                        for anchor in soup.find_all("a", href=True)[:200]:
                            candidate = urljoin(str(response.url), anchor["href"])
                            cparsed = urlparse(candidate)
                            if cparsed.scheme in {"http", "https"}:
                                if not request.same_domain_only or cparsed.netloc in allowed_domains:
                                    queue.append((candidate, depth + 1))
                except Exception:
                    failed += 1

        indexed, duplicates = index_service.add_many(items)
        return CrawlResponse(
            crawled=len(seen),
            indexed=indexed,
            skipped=skipped + duplicates,
            failed=failed,
            took_ms=round((time.perf_counter() - t0) * 1000, 2),
        )


crawler = Crawler()
