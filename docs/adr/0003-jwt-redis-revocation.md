# ADR-0003: JWT Authentication with Redis JTI Revocation

**Date:** 2026-02-10  
**Status:** Accepted  
**Deciders:** Kumar (Lead)

---

## Context

Stateless JWTs cannot be invalidated before expiry. Logout must be instant for security — a stolen token valid for 24h is unacceptable.

## Decision

Issue JWTs with a **JTI (JWT ID)** claim. On logout, store the JTI in a Redis set with TTL matching token expiry. Every authenticated request checks the blacklist via `Redis.sismember`. 

## Consequences

- **Positive:** O(1) revocation check; true logout; audit trail possible via JTI log.
- **Negative:** Redis becomes a hard dependency for auth. Mitigated by Redis Sentinel/Cluster in prod.
- **Alternative rejected:** Short-lived tokens (5min) + refresh — adds client complexity and worse UX.
