from app.rag.agentic_rag.planner import has_freshness_term
from app.rag.agentic_rag.schemas import AnswerabilityResult, Evidence


class AnswerabilityEvaluator:
    def evaluate(self, query: str, evidences: list[Evidence]) -> AnswerabilityResult:
        if not evidences:
            return AnswerabilityResult(
                answerable=False,
                confidence=0.0,
                reason="No evidence is available to answer the query.",
                web_queries=[query],
            )

        if has_freshness_term(query):
            return AnswerabilityResult(
                answerable=False,
                confidence=0.35,
                reason="Query asks for fresh information, so web evidence is required.",
                web_queries=[query],
            )

        return AnswerabilityResult(
            answerable=True,
            confidence=0.75,
            reason="Local evidence is available and query does not require freshness.",
            web_queries=[],
        )
