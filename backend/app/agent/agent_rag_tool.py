"""Agent 侧 search_rag 工具：RAG 二次检索 + 请求级护栏。

护栏目标：
- 同 query 短路：query 字符归一化后命中「本轮已检索集合」→ 不重跑，返回提示；
- 请求级限次：单请求最多执行 MAX_RAG_CALLS 次真实 RAG 检索；
- 护栏状态存 ContextVar，随请求创建、请求结束即弃；无守卫上下文时放行
  （兼容 get_agent_response 非流式路径与 scripts/ 评测脚本直调）。
"""
import inspect
import re
from contextvars import ContextVar
from typing import Any

from langchain_core.tools import tool

from app.agent.agent_tools import (
    get_current_user_id_from_context,
    get_thinking_callback_from_context,
)

MAX_RAG_CALLS = 2

_DEDUP_HINT = "该检索角度已在本轮资料中覆盖。请直接基于已提供的参考资料回答；如需深入，请换一个更聚焦的新角度检索。"
_LIMIT_HINT = f"已检索过 {MAX_RAG_CALLS} 次，请基于现有资料回答，不要继续检索。"

rag_guard_var: ContextVar[dict[str, Any] | None] = ContextVar("rag_guard", default=None)


def reset_rag_guard() -> None:
    """清空护栏（每轮请求开始/测试收尾用）。"""
    rag_guard_var.set(None)


def normalize_query(query: str) -> str:
    """query 字符归一化：去空白/去常见全半角标点 + 小写，用于同 query 短路比对。

    当前仅做字符串归一化后的精确匹配；后续可引入 embedding/LLM 相似度判断，完善语义重复拦截。
    """
    return re.sub(r"[\s，。！？!?、；：:;，,．.、'\"“”‘’（）()【】\[\]]+", "", query).lower()


def init_rag_guard(rag_searched_queries: list[str] | None) -> None:
    """初始化请求级护栏：searched 预置前置管线已检索的 query（字符归一化后）。"""
    rag_guard_var.set(
        {"count": 0, "searched": {normalize_query(q) for q in (rag_searched_queries or [])}}
    )


def _format_supplementary_retrieval(
    query: str,
    status: str,
    evidence: str = "无",
    source_summary: str | None = None,
    used_web: bool | None = None,
    note: str | None = None,
) -> str:
    """构造稳定的 ToolMessage 文本，区分新证据与检索状态提示。"""
    lines = [
        "[补充检索结果开始]",
        f"检索角度：{query}",
        f"检索状态：{status}",
    ]
    if source_summary is not None:
        lines.append(f"证据来源概况：{source_summary}")
    if used_web is not None:
        lines.append(f"是否包含外部搜索：{'是' if used_web else '否'}")
    if note is not None:
        lines.append(f"状态说明：{note}")
    lines.extend(["", "[检索证据]", evidence, "[补充检索结果结束]"])
    return "\n".join(lines)


def build_pre_searched_queries(original_query: str, rag_result) -> list[str]:
    """从前置 AgenticRagResult 提取「本轮实际检索用过的 query」。

    仅当 plan.need_retrieval=True 时纳入用户原 query 与各检索步 query；
    answerability.web_queries 只要有就纳入（web 兜底实际发生过检索）。
    rag_result 可为 None（管线失败降级）。
    """
    if rag_result is None:
        return []
    plan = getattr(rag_result, "plan", None)
    if plan is None:
        return []
    queries: list[str] = []
    if getattr(plan, "need_retrieval", False):
        queries.append(original_query)
        for step in getattr(plan, "steps", []) or []:
            q = getattr(step, "query", "")
            if q:
                queries.append(q)
    answerability = getattr(rag_result, "answerability", None)
    if answerability is not None:
        queries.extend(getattr(answerability, "web_queries", None) or [])
    return [q for q in queries if q]


@tool(description=(
    "在你的本地知识库/笔记/知识图谱中做补充检索，返回带来源标注的证据摘要。"
    "仅在已有参考资料不足、需按新维度深挖、或需验证具体事实时调用；"
    "参数 query 必须是本轮尚未检索过的新聚焦角度，不要重复已覆盖的问题。"
))
async def search_rag(query: str) -> str:
    """Agent 自主二次检索工具：复用 AgenticRagService 全链路。"""
    user_id = get_current_user_id_from_context()
    if not user_id:
        return _format_supplementary_retrieval(
            query,
            "无法确定用户身份（非事实证据）",
            note="错误: 无法确定用户身份",
        )

    guard = rag_guard_var.get()
    if guard is not None:
        # 当前护栏只拦截归一化后的精确重复；语义重复拦截留待后续完善。
        if normalize_query(query) in guard["searched"]:
            return _format_supplementary_retrieval(
                query,
                "检索角度已覆盖（非事实证据）",
                note=_DEDUP_HINT,
            )
        if guard["count"] >= MAX_RAG_CALLS:
            return _format_supplementary_retrieval(
                query,
                "检索已达上限（非事实证据）",
                note=_LIMIT_HINT,
            )

    try:
        from app.rag.agentic_rag.evidence import _SOURCE_LABELS
        from app.rag.agentic_rag.service import AgenticRagService

        result = await AgenticRagService().run(
            query,
            user_id,
            thinking_callback=get_thinking_callback_from_context(),
        )
    except Exception as e:
        return _format_supplementary_retrieval(
            query,
            "检索失败（非事实证据）",
            note=f"检索失败: {str(e)}",
        )

    if guard is not None:
        guard["count"] += 1
        guard["searched"].add(normalize_query(query))

    if not result.context or not result.evidences:
        return _format_supplementary_retrieval(
            query,
            "未找到证据（非事实证据）",
            "无",
            source_summary="无证据",
            used_web=bool(getattr(result, "used_web", False)),
        )

    counts: dict[str, int] = {}
    for evidence in getattr(result, "evidences", []) or []:
        source = getattr(evidence, "source", "unknown")
        counts[source] = counts.get(source, 0) + 1
    parts = [f"{_SOURCE_LABELS.get(src, src)} {cnt} 条" for src, cnt in counts.items()]
    summary = "、".join(parts) or "无证据"
    if getattr(result, "used_web", False):
        summary += "（含外部搜索）"

    callback = get_thinking_callback_from_context()
    if callback is not None:
        event = {
            "type": "thinking",
            "stage": "supplemental_retrieval",
            "content": "补充检索完成",
            "details": {
                "query": query,
                "status": "evidence",
                "evidence_count": len(result.evidences),
                "results": [
                    {
                        "id": evidence.id,
                        "source": evidence.source,
                        "title": evidence.title,
                        "score": evidence.score,
                        "url": evidence.url,
                        "preview": evidence.content[:500],
                    }
                    for evidence in result.evidences
                ],
            },
        }
        callback_result = callback(event)
        if inspect.isawaitable(callback_result):
            await callback_result

    return _format_supplementary_retrieval(
        query,
        "已获取证据",
        result.context,
        source_summary=summary,
        used_web=bool(getattr(result, "used_web", False)),
    )


def set_tool_thinking_callback_for_test(callback) -> None:
    """仅测试用：临时覆盖当前 thinking_callback 上下文，验证 search_rag 回传。"""
    from app.agent import agent_tools as _at

    _at.set_thinking_callback(callback)
