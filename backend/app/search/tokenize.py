from __future__ import annotations

import re
from collections import Counter

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.#-]{1,}")

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "to", "of", "in",
    "on", "at", "by", "with", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "it", "this", "that", "these", "those", "you", "your", "we", "our", "they", "their", "he",
    "she", "i", "me", "my", "can", "could", "would", "should", "will", "may", "might", "do",
    "does", "did", "not", "no", "yes", "about", "into", "over", "under", "than", "too", "very",
}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS]


def term_counts(text: str) -> Counter[str]:
    return Counter(tokenize(text))
