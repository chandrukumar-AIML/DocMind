# ADR-0002: LangGraph 12-Node Agent Graph

**Date:** 2026-02-01  
**Status:** Accepted  
**Deciders:** Kumar (Lead)

---

## Context

Simple chain-of-thought prompting is insufficient for complex document queries requiring multi-step reasoning, hallucination checking, and query decomposition.

## Decision

Implement a **12-node LangGraph StateGraph** with the following nodes:

| Node | Responsibility |
|------|---------------|
| `query_decomposer` | Break complex queries into sub-questions |
| `hyde_generator` | Generate Hypothetical Document Embeddings for better dense retrieval |
| `sparse_retriever` | BM25 keyword retrieval |
| `dense_retriever` | ChromaDB/FAISS semantic retrieval |
| `rrf_merger` | Reciprocal Rank Fusion |
| `reranker` | Cross-encoder reranking |
| `crag_grader` | Corrective RAG: grade context relevance, trigger web search if needed |
| `self_rag_checker` | Self-RAG: grade generation, loop if score low |
| `hallucination_checker` | Verify answer is grounded in retrieved context |
| `answer_generator` | Final LLM call with graded context |
| `citation_extractor` | Map answer spans back to source chunks |
| `response_formatter` | Structure final output with citations |

## Checkpointer priority ladder

`PostgresSaver` (prod) → `RedisSaver` (fast cache) → `MemorySaver` (dev fallback).  
Enables conversation persistence and mid-graph human-in-the-loop interruption.

## Consequences

- **Positive:** CRAG + Self-RAG loops measurably reduce hallucination rate.
- **Negative:** Adds graph compilation overhead at startup (~2s). Mitigated by compiling once at lifespan.
- **Trade-off:** 12 nodes vs simpler chain — justified by the product promise of "accurate AI on your docs."
