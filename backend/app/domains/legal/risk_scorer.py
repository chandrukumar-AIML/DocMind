# backend/app/domains/legal/risk_scorer.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Scalability

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Optional

# FIXED: Move numpy import to module level
import numpy as np

# DVMELTSS-M: Import centralized utilities
from app.core.domain_utils import (
    build_domain_prompt,
    safe_parse_llm_json,
    get_domain_llm,
    validate_legal_output,
    generate_domain_correlation_id,
)
from app.core.retry import retry_async, RetryConfig

from .clause_extractor import ExtractedClause, ClauseExtractionResult

logger = logging.getLogger(__name__)

RISK_SCORING_PROMPT: Final = """You are a legal risk analyst. Score this contract clause for risk.

Clause type: {clause_type}
Section: {section_ref}
Clause text: {clause_text}
Specific values: {specific_values}

Return ONLY valid JSON:
{{
  "risk_score": 7.5,
  "risk_level": "high|medium|low",
  "risk_explanation": "2-sentence explanation of why this is risky",
  "red_flags": ["specific concern 1", "specific concern 2"],
  "recommendation": "what should be changed or negotiated"
}}

Risk scoring guide (1-10):
1-3: Low risk — standard boilerplate, favorable terms
4-6: Medium risk — some exposure, negotiable
7-8: High risk — significant liability, should be reviewed by counsel
9-10: Critical risk — one-sided, potentially unenforceable, do not sign

Consider:
- Missing reciprocity (one-sided obligations)
- Missing liability caps
- Unfavorable jurisdiction
- Unlimited indemnification
- Broad IP assignment
- Very short notice periods
"""


@dataclass
class ClauseRiskReport:
    """Risk assessment for a single clause."""

    clause_type: str
    section_ref: str
    risk_score: float  # 1.0–10.0
    risk_level: str  # low / medium / high / critical
    risk_explanation: str
    red_flags: list[str]
    recommendation: str
    correlation_id: str = ""  # FIXED: Added for tracing


@dataclass
class DocumentRiskReport:
    """Aggregate risk report for a full contract."""

    source_file: str
    overall_risk_score: float
    clause_reports: list[ClauseRiskReport]
    missing_clauses: list[str]
    critical_clauses: list[str]  # clause refs with score >= 9
    high_risk_clauses: list[str]  # clause refs with score >= 7
    executive_summary: str
    correlation_id: str = ""  # FIXED: Added for tracing

    @property
    def risk_level(self) -> str:
        if self.overall_risk_score >= 7.5:
            return "critical"
        elif self.overall_risk_score >= 5.5:
            return "high"
        elif self.overall_risk_score >= 3.5:
            return "medium"
        return "low"


class RiskScorer:
    """
    Scores legal clauses and generates contract risk reports.
    Evaluates each clause independently then aggregates to document level.
    """

    # FIXED: Move weights to class level constant
    _WEIGHTS: Final = {
        "liability_cap": 2.0,
        "indemnification": 2.0,
        "termination": 1.5,
        "payment_terms": 1.5,
        "governing_law": 1.2,
        "other": 1.0,
    }

    def __init__(self, model: str = "gpt-4o"):
        # FIXED: Use centralized LLM pool
        self.llm = get_domain_llm(streaming=False, model_override=model)
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=2,
                backoff_base=0.5,
                exceptions=(Exception,),
            )
        )

    async def score_clause(
        self,
        clause: ExtractedClause,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> ClauseRiskReport:
        """Score a single clause for risk (1-10)."""
        corr_id = correlation_id or generate_domain_correlation_id("legal")

        prompt = build_domain_prompt(
            RISK_SCORING_PROMPT,
            clause_type=clause.clause_type,
            section_ref=clause.section_ref or "unspecified",
            clause_text=clause.text[:600],
            specific_values=", ".join(clause.specific_values) or "none identified",
        )

        try:
            # FIXED: Apply retry + centralized JSON parsing
            response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
            data = safe_parse_llm_json(response.content, default={})

            is_valid, error = validate_legal_output(data)
            if not is_valid:
                logger.warning(f"[{corr_id}] Invalid risk output: {error}")
                # Return safe default
                clause.risk_score = 5.0
                return ClauseRiskReport(
                    clause_type=clause.clause_type,
                    section_ref=clause.section_ref,
                    risk_score=5.0,
                    risk_level="medium",
                    risk_explanation="Risk assessment unavailable.",
                    red_flags=[],
                    recommendation="Manual review recommended.",
                    correlation_id=corr_id,
                )

            score = float(data.get("risk_score", 5.0))
            score = max(1.0, min(10.0, score))

            clause.risk_score = score
            return ClauseRiskReport(
                clause_type=clause.clause_type,
                section_ref=clause.section_ref,
                risk_score=score,
                risk_level=data.get("risk_level", "medium"),
                risk_explanation=str(data.get("risk_explanation", ""))[:200],
                red_flags=[str(f) for f in data.get("red_flags", [])[:5]],
                recommendation=str(data.get("recommendation", ""))[:200],
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.warning(f"[{corr_id}] Risk scoring failed: {e}")
            clause.risk_score = 5.0
            return ClauseRiskReport(
                clause_type=clause.clause_type,
                section_ref=clause.section_ref,
                risk_score=5.0,
                risk_level="medium",
                risk_explanation="Risk assessment unavailable.",
                red_flags=[],
                recommendation="Manual review recommended.",
                correlation_id=corr_id,
            )

    async def score_document(
        self,
        extraction: ClauseExtractionResult,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> DocumentRiskReport:
        """Score all clauses and generate document-level risk report."""
        corr_id = correlation_id or extraction.correlation_id or generate_domain_correlation_id("legal")

        clause_reports = []
        for clause in extraction.clauses:
            report = await self.score_clause(clause, corr_id)
            clause_reports.append(report)

        # Weight liability + indemnification higher in overall score
        weighted_scores = []
        for r in clause_reports:
            w = self._WEIGHTS.get(r.clause_type, 1.0)
            weighted_scores.extend([r.risk_score] * int(w * 10))

        # Add risk for missing standard clauses
        missing_penalty = len(extraction.missing_standard_clauses) * 0.5
        base_score = float(np.mean(weighted_scores)) if weighted_scores else 5.0
        overall = min(10.0, base_score + missing_penalty)

        critical = [r.section_ref or r.clause_type for r in clause_reports if r.risk_score >= 9.0]
        high = [r.section_ref or r.clause_type for r in clause_reports if 7.0 <= r.risk_score < 9.0]

        # Generate executive summary
        summary = await self._generate_summary(
            source_file=extraction.source_file,
            overall_score=overall,
            clause_reports=clause_reports,
            missing=extraction.missing_standard_clauses,
            correlation_id=corr_id,
        )

        return DocumentRiskReport(
            source_file=extraction.source_file,
            overall_risk_score=round(overall, 2),
            clause_reports=clause_reports,
            missing_clauses=extraction.missing_standard_clauses,
            critical_clauses=critical,
            high_risk_clauses=high,
            executive_summary=summary,
            correlation_id=corr_id,  # FIXED: Propagate correlation_id
        )

    async def _generate_summary(
        self,
        source_file: str,
        overall_score: float,
        clause_reports: list[ClauseRiskReport],
        missing: list[str],
        correlation_id: str,
    ) -> str:
        """Generate a plain-English executive summary of contract risk."""
        top_risks = sorted(clause_reports, key=lambda r: r.risk_score, reverse=True)[:3]
        risks_text = "\n".join(
            f"- {r.clause_type}: score {r.risk_score:.1f} — {r.risk_explanation[:100]}" for r in top_risks
        )
        missing_text = f"Missing clauses: {', '.join(missing)}" if missing else ""

        prompt = build_domain_prompt(
            """Write a 3-sentence executive summary of this contract's legal risk.

Document: {source_file}
Overall risk score: {overall_score}/10

Top risks:
{risks_text}

{missing_text}

Be concise and use plain English. Focus on business impact.""",
            source_file=source_file,
            overall_score=f"{overall_score:.1f}",
            risks_text=risks_text,
            missing_text=missing_text,
        )

        try:
            response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
            return response.content.strip()[:300]
        except Exception:
            return (
                f"Contract risk score: {overall_score:.1f}/10. "
                f"{len(top_risks)} high-risk clauses identified. "
                f"Manual legal review recommended."
            )


# DVMELTSS-M: Explicit module exports
__all__ = ["RiskScorer", "ClauseRiskReport", "DocumentRiskReport"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
