# ADR-0001: Hybrid RAG Architecture (BM25 + Dense + RRF)

**Date:** 2026-01-15  
**Status:** Accepted  
**Deciders:** Kumar (Lead), DocMind engineering

---

## Context

DocMind needs to retrieve relevant document chunks for user queries. Pure dense retrieval (embeddings) fails on exact keyword searches (contract numbers, dates, proper nouns). Pure sparse retrieval (BM25) misses semantic paraphrases.

## Decision

Use **hybrid retrieval**: BM25 sparse + ChromaDB/FAISS dense in parallel, merged with **Reciprocal Rank Fusion (RRF)**, followed by a **cross-encoder reranker** for final top-k selection.

```
Query → [BM25 sparse] + [Dense FAISS/Chroma]
                ↓ RRF fusion
         Merged candidate list
                ↓ Cross-encoder reranker
              Top-k chunks → LLM
```

## Rationale

- RRF is parameter-free and consistently outperforms weighted score combination without tuning.
- Cross-encoder reranking costs one extra inference pass but doubles precision@5 in internal benchmarks.
- BM25 via `rank-bm25` runs in-process — no additional service.

## Consequences

- **Positive:** Better recall for both keyword and semantic queries.
- **Negative:** Two retrieval passes + reranking adds ~80ms latency. Acceptable for doc-chat UX (sub-2s end-to-end).
- **Trade-off accepted:** If latency becomes a problem, reranker can be gated behind a flag.
