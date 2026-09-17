from __future__ import annotations

from app.services.crawler import (
    extract_page_documents,
    is_low_value_block,
    is_probable_localized_path,
)


def test_localized_paths_are_filtered_but_normal_docs_paths_are_not():
    assert is_probable_localized_path("https://opentelemetry.io/pt/docs/")
    assert is_probable_localized_path("https://kubernetes.io/zh-cn/docs/concepts/")
    assert not is_probable_localized_path("https://opentelemetry.io/docs/concepts/signals/traces/")
    assert not is_probable_localized_path("https://react.dev/learn")


def test_navigation_markers_are_low_value():
    assert is_low_value_block(
        "Navigation",
        "index modules | next | previous | Python Documentation | Theme Auto Light Dark |",
    )


def test_real_explanatory_section_is_kept():
    assert not is_low_value_block(
        "Readiness probes",
        "Readiness probes determine whether a container is ready to accept network traffic.",
    )


def test_extractor_drops_navigation_chunk_and_keeps_content():
    html = """
    <html>
      <head><title>Iterator docs</title></head>
      <body>
        <main>
          <h2>Navigation</h2>
          <p>index modules | next | previous | Python Documentation | Theme Auto Light Dark |</p>
          <h2>Iterators</h2>
          <p>An iterator is an object that produces values one at a time while preserving iteration state.</p>
          <p>Calling next() advances the iterator until it is exhausted and raises StopIteration.</p>
        </main>
      </body>
    </html>
    """
    docs = extract_page_documents(html, "https://docs.python.org/3/test", "test")
    assert docs
    joined = " ".join(doc.text for doc in docs)
    assert "iterator is an object" in joined
    assert "Theme Auto Light Dark" not in joined
