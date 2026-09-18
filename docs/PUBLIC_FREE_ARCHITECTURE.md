# Nexus Public Free-Tier Architecture

Nexus is intentionally usable without login. Anonymous search is the product.

## Zero-paid-AI mode

```text
PUBLIC_GENERATION_ENABLED=false
SEMANTIC_PROVIDER=local
```

This keeps the public answer path extractive/deterministic and avoids paid LLM or embedding calls.

## Anonymous traffic protection

The application includes a dependency-free process-local token bucket as a backstop for `/api/search` and `/api/ask`.

Recommended edge:

```text
Internet -> Cloudflare free CDN/WAF/rate limiting -> Nexus coordinator -> private shards
```

The application limiter is not a replacement for an edge WAF.

## No-account principle

```text
open Nexus -> type query -> search -> evidence -> answer/caveat/abstain
```

No signup, OAuth, email, or account is required.

## Cost boundary

Nexus can avoid paid AI APIs, but no hosting architecture can guarantee zero infrastructure cost at arbitrary traffic. Free tiers have quotas. Large real-world usage eventually creates compute, storage, and network cost.
