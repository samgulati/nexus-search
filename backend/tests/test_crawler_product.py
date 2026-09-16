from app.services.crawler import canonicalize_url, chunk_text, extract_page_documents


def test_canonicalize_url_removes_tracking_and_fragments():
    url = canonicalize_url("https://Example.com/docs/page?utm_source=x&keep=1#section")
    assert url == "https://example.com/docs/page?keep=1"


def test_canonicalize_url_skips_binary_assets():
    assert canonicalize_url("https://example.com/file.pdf") == ""
    assert canonicalize_url("https://example.com/image.png") == ""


def test_chunk_text_creates_bounded_overlapping_chunks():
    text = " ".join(
        f"Sentence {i} explains a distributed systems concept with enough detail."
        for i in range(120)
    )
    chunks = chunk_text(text, target_chars=500, overlap_chars=80)
    assert len(chunks) > 2
    assert all(len(chunk) >= 120 for chunk in chunks)
    assert all(len(chunk) < 700 for chunk in chunks)


def test_extract_page_documents_prefers_main_content_and_sections():
    html = """
    <html>
      <head><title>Kafka Guide</title></head>
      <body>
        <nav>Navigation noise should disappear entirely from indexed content.</nav>
        <main>
          <h1>Consumers</h1>
          <p>Kafka consumers read records from partitions and maintain offsets for progress tracking. This paragraph contains enough useful technical information to be indexed as documentation evidence for search users.</p>
          <h2>Retries</h2>
          <p>When processing fails, consumers can retry work, but idempotent handling is important because records may be delivered more than once under at-least-once processing semantics.</p>
        </main>
        <footer>Footer noise should disappear entirely.</footer>
      </body>
    </html>
    """
    docs = extract_page_documents(html, "https://kafka.apache.org/guide", "guide")
    assert docs
    combined = " ".join(d.text for d in docs)
    assert "Navigation noise" not in combined
    assert "Footer noise" not in combined
    assert any("Consumers" in d.title or "Retries" in d.title for d in docs)
    assert all(d.source == "trusted-docs:kafka.apache.org" for d in docs)
