# Agentic RAG Web Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first Agentic RAG layer that plans retrieval, normalizes local and web evidence, evaluates answerability, and feeds an explainable evidence context into the existing chat stream.

**Architecture:** Add a new `app.rag.agentic_rag` package beside the current `RagService`. The first version keeps Chroma, note search, rerank, Agent streaming, and SSE response shape intact, while replacing route-level fixed pre-RAG with a controlled planner-first orchestration service.

**Tech Stack:** FastAPI, LangChain/LangChain Classic, Pydantic v2, Chroma, SQLAlchemy async sessions, httpx, pytest.

**Spec:** Chat-approved design from 2026-08-23 conversation; no separate design spec file exists.

## Global Constraints

- Work on branch `feat/migrate_to_agentic_rag`.
- Keep current RAG, note, knowledge-base, and Agent APIs backward-compatible unless a task explicitly changes them.
- Default web search must be disabled: `WEB_SEARCH_ENABLED=false` unless configured.
- Do not send full private note or knowledge-base contents to web search; web queries must come from the user query or short planner/evaluator-generated keywords.
- Web search results are temporary evidence only; do not persist them to MySQL, Chroma, or future graph storage.
- Planner JSON parsing must have deterministic rule-based fallback.
- Every final evidence item must carry a `source` field distinguishing `note`, `knowledge_base`, or `web`.
- Prefer small focused modules under `backend/app/rag/agentic_rag/` over expanding `rag_service.py`.
- Use TDD: write/verify failing tests before production code for each task.

---

### Task 1: Agentic RAG Contracts

**Files:**
- Create: `backend/app/rag/agentic_rag/__init__.py`
- Create: `backend/app/rag/agentic_rag/schemas.py`
- Test: `backend/tests/rag/test_agentic_rag_schemas.py`

**Interfaces:**
- Produces: `Evidence`, `RetrievalStep`, `RetrievalPlan`, `AnswerabilityResult`, `AgenticRagResult` Pydantic models.
- Later tasks must import contracts only from `app.rag.agentic_rag.schemas`.

**Requirements:**
- `Evidence.source` must allow only `note`, `knowledge_base`, and `web`.
- `Evidence` fields: `id: str`, `source`, `title: str`, `content: str`, `score: float | None = None`, `url: str | None = None`, `metadata: dict[str, Any] = Field(default_factory=dict)`.
- `RetrievalStep` fields: `tool: Literal["search_notes", "search_knowledge_base", "hybrid_search", "web_search"]`, `query: str`, `top_k: int = 5`.
- `RetrievalPlan` fields: `need_retrieval: bool`, `steps: list[RetrievalStep]`, `allow_web_fallback: bool = False`, `reason: str = ""`.
- `AnswerabilityResult` fields: `answerable: bool`, `confidence: float`, `reason: str`, `web_queries: list[str] = Field(default_factory=list)`.
- `AgenticRagResult` fields: `context: str`, `evidences: list[Evidence]`, `plan: RetrievalPlan`, `answerability: AnswerabilityResult | None = None`, `used_web: bool = False`.

**Verification:**
- Run: `pytest backend/tests/rag/test_agentic_rag_schemas.py -v`

### Task 2: Local Evidence Retrieval and Fusion

**Files:**
- Create: `backend/app/rag/agentic_rag/local_retriever.py`
- Create: `backend/app/rag/agentic_rag/evidence.py`
- Test: `backend/tests/rag/test_agentic_rag_local_retriever.py`
- Test: `backend/tests/rag/test_agentic_rag_evidence.py`

**Interfaces:**
- Consumes: schemas from Task 1.
- Produces: `LocalRetriever.search(user_id: str, steps: list[RetrievalStep]) -> list[Evidence]`.
- Produces: `merge_evidence(evidences: list[Evidence], limit: int = 8) -> list[Evidence]`.
- Produces: `format_evidence_context(evidences: list[Evidence], max_chars: int = 6000) -> str`.

**Requirements:**
- Use existing `init_manager.note_service.search_notes` for note retrieval.
- Use existing `VectorStoreService().get_retriever(query, user_id)` for knowledge-base/hybrid retrieval.
- Convert note results and LangChain `Document` results to `Evidence`.
- Dedupe by `(source, id)` first, then by identical normalized content.
- Formatted context must label sources in Chinese: `来源：笔记`, `来源：知识库`, `来源：外部搜索`.
- Local evidence must not call web search.

**Verification:**
- Run: `pytest backend/tests/rag/test_agentic_rag_local_retriever.py backend/tests/rag/test_agentic_rag_evidence.py -v`

### Task 3: Planner and Answerability Evaluator

**Files:**
- Create: `backend/app/rag/agentic_rag/planner.py`
- Create: `backend/app/rag/agentic_rag/evaluator.py`
- Create: `backend/app/prompt/agentic_rag_planner.txt`
- Create: `backend/app/prompt/agentic_rag_answerability.txt`
- Modify: `backend/app/config/prompt.yaml`
- Test: `backend/tests/rag/test_agentic_rag_planner.py`
- Test: `backend/tests/rag/test_agentic_rag_evaluator.py`

**Interfaces:**
- Consumes: schemas from Task 1.
- Produces: `AgenticRagPlanner.plan(query: str) -> RetrievalPlan`.
- Produces: `AnswerabilityEvaluator.evaluate(query: str, evidences: list[Evidence]) -> AnswerabilityResult`.

**Requirements:**
- Planner should prefer LLM JSON output when available.
- Planner must fall back deterministically when LLM output is invalid or model call fails.
- Rule fallback: casual greetings do not retrieve; otherwise use one `hybrid_search` step with the original query and `allow_web_fallback=True` for freshness terms.
- Freshness terms include Chinese and English terms: `最新`, `现在`, `今天`, `今年`, `版本`, `价格`, `新闻`, `latest`, `current`, `today`, `price`, `version`, `news`.
- Evaluator should return not answerable if no evidence is present.
- Evaluator should request web search when freshness terms are present or evidence is empty.

**Verification:**
- Run: `pytest backend/tests/rag/test_agentic_rag_planner.py backend/tests/rag/test_agentic_rag_evaluator.py -v`

### Task 4: Web Search Fallback

**Files:**
- Create: `backend/app/rag/agentic_rag/web_search.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/rag/test_agentic_rag_web_search.py`

**Interfaces:**
- Consumes: `Evidence` from Task 1.
- Produces: `WebSearchClient.search(query: str, max_results: int = 5) -> list[Evidence]`.

**Requirements:**
- Use `httpx.AsyncClient`; do not add a search SDK dependency.
- Support `WEB_SEARCH_PROVIDER=tavily` and `WEB_SEARCH_PROVIDER=serper`.
- Return `[]` when `WEB_SEARCH_ENABLED` is not true or provider/key is missing.
- Convert search results to `Evidence(source="web")` with `title`, `url`, `content`, and metadata containing provider.
- Never persist web search results.

**Verification:**
- Run: `pytest backend/tests/rag/test_agentic_rag_web_search.py -v`

### Task 5: Agentic RAG Orchestration Service

**Files:**
- Create: `backend/app/rag/agentic_rag/service.py`
- Test: `backend/tests/rag/test_agentic_rag_service.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: `AgenticRagService.run(query: str, user_id: str, thinking_callback: Callable | None = None) -> AgenticRagResult`.

**Requirements:**
- Emit thinking events for stages: `agentic_plan`, `local_retrieval`, `answerability`, optional `web_search`, `evidence_fusion`, `context_ready`.
- If planner says no retrieval, return empty context and no web search.
- Always do local retrieval before web search.
- Call web search only when plan allows fallback and evaluator says not answerable or provides web queries.
- Merge local and web evidence, with local evidence ordered before web evidence when comparable.

**Verification:**
- Run: `pytest backend/tests/rag/test_agentic_rag_service.py -v`

### Task 6: Chat Route and Agent Prompt Integration

**Files:**
- Modify: `backend/app/router/chat.py`
- Modify: `backend/app/agent/agent.py`
- Test: `backend/tests/test_chat_api.py`
- Test: `backend/tests/agent/test_agent.py`

**Interfaces:**
- Consumes: `AgenticRagService.run` from Task 5.
- Keeps: `/chat/agent/query/stream` API shape and SSE `response`/`done` frames.

**Requirements:**
- Replace route-level `compute_route_score + RagService` pre-RAG path with `AgenticRagService`.
- Preserve session handling and `get_agent_stream_response` final generation.
- Preserve existing unauthenticated behavior.
- Agent system prompt must instruct the model to distinguish local evidence from external search evidence and state when evidence is insufficient.

**Verification:**
- Run: `pytest backend/tests/test_chat_api.py backend/tests/agent/test_agent.py -v`

### Task 7: Final Regression and Branch Review

**Files:**
- Modify only if final review finds defects.

**Interfaces:**
- Consumes all previous tasks.

**Requirements:**
- Run full focused regression for RAG, chat, and agent tests.
- Dispatch a final reviewer over the whole branch diff.

**Verification:**
- Run: `pytest backend/tests/rag backend/tests/test_chat_api.py backend/tests/agent -v`
