from __future__ import annotations

import pytest

from app.models import CrawlRequest, DocumentIn
from app.services.index_service import IndexService


def item(url: str, text: str, title: str = "Doc") -> DocumentIn:
    return DocumentIn(
        title=title,
        text=text,
        url=url,
        source="trusted-docs:example.com",
    )


def test_crawl_refresh_defaults_off():
    request = CrawlRequest(seeds=["https://example.com/docs"])
    assert request.refresh_existing is False


def test_replace_url_removes_stale_chunks_and_keeps_other_urls():
    service = IndexService()
    url = "https://example.com/a"
    other = "https://example.com/b"
    service.add_many([
        item(url, "old alpha content that is long enough to index safely"),
        item(url, "old beta content that is long enough to index safely"),
        item(other, "other page content that must remain after a refresh"),
    ])
    added, skipped, removed, unchanged = service.replace_url(
        url,
        [item(url, "new canonical content that replaces stale chunks")],
    )
    assert unchanged is False
    assert removed == 2
    assert added == 1
    assert skipped == 0
    assert len([d for d in service.documents.values() if d.url == url]) == 1
    assert len([d for d in service.documents.values() if d.url == other]) == 1


def test_replace_url_is_noop_when_effective_page_is_unchanged():
    service = IndexService()
    url = "https://example.com/a"
    doc = item(url, "same canonical content that should not trigger rebuild churn")
    service.add_many([doc])
    added, skipped, removed, unchanged = service.replace_url(url, [doc])
    assert unchanged is True
    assert added == 0
    assert skipped == 1
    assert removed == 0
    assert len(service.documents) == 1


def test_replace_url_rejects_empty_refresh_to_prevent_accidental_erasure():
    service = IndexService()
    with pytest.raises(ValueError):
        service.replace_url("https://example.com/a", [])


def test_replace_url_rejects_cross_url_documents():
    service = IndexService()
    with pytest.raises(ValueError):
        service.replace_url(
            "https://example.com/a",
            [item("https://example.com/b", "valid body but belongs to another URL")],
        )
