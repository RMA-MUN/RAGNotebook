"""多跳问答 + GraphRAG vs 纯文本检索增量评测。

从图中抽 2 跳路径 a -r1-> b -r2-> c（gold 中间实体=b），构造问题：
    “根据知识图谱，实体“a”和“c”之间是如何关联的？请指出中间实体与关系。”
分别跑两条检索路（固定 planner，控制变量）：
    - 图路径：steps=[search_graph]   → 实体证据 + 实体提到的 chunk
    - 文本路径：steps=[hybrid_search] → chunk 证据
指标（如实反映当前检索实现——search_graph 做「实体匹配+chunk 提及」，不做关系邻居扩展）：
    - a/c 命中：图路径是否把问题提到的两端实体都作为图实体召回
    - 中间实体信息覆盖：检索证据文本中是否出现 b（能否支撑回答）
    - 端到端答案正确率：各自上下文生成回答，LLM 裁判是否指出中间实体 b（0/1），及其增量

⚠️ 口径：题目从图中真实路径生成，gold 是图中真实链；衡量「GraphRAG 相对纯文本的增量」。
当前实现的图检索不做多跳邻居扩展，故不测「图路径返回 b 实体」这种结构性不成立的指标。

读：Neo4j（--user-id）
写：results/eval_graph_multihop/report.json + report.md
"""
import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from app.graph.storage.neo4j_client import get_neo4j_driver  # noqa: E402
from app.rag.agentic_rag.schemas import RetrievalPlan, RetrievalStep  # noqa: E402
from app.rag.agentic_rag.service import AgenticRagService  # noqa: E402
from app.rag.agentic_rag.web_search import WebSearchClient  # noqa: E402
from app.utils.factory import ChatModelFactory  # noqa: E402

JUDGE_PROMPT = (
    "你是多跳问答裁判。已知问题要求找出连接实体 a 与 c 的中间实体 b（gold）。"
    "判断【回答】是否正确指出了中间实体（是否包含/等价于 gold）。只返回 JSON：\n"
    '{{"correct": 1 或 0, "reason": "..."}}\n\n'
    "问题：{question}\ngold 中间实体：{gold}\n检索证据：{evidence}\n回答：{answer}"
)


class FixedPlanner:
    """固定检索步骤的规划器（评测控制变量用，plan 不调 LLM）。"""

    def __init__(self, steps: list[RetrievalStep]):
        self.steps = steps

    async def plan(self, query: str) -> RetrievalPlan:
        steps = [RetrievalStep(tool=s.tool, query=query, top_k=s.top_k) for s in self.steps]
        return RetrievalPlan(need_retrieval=True, steps=steps,
                             allow_web_fallback=False, reason="fixed-for-eval")


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no json")
    return json.loads(m.group(0))


async def _ask(chat, messages) -> str:
    resp = await chat.ainvoke(messages)
    c = getattr(resp, "content", resp)
    return c if isinstance(c, str) else str(c)


async def _gen_answer(chat, context: str, q: str) -> str:
    system = (
        "你是用户的智能助手。\n\n以下是与用户问题相关的参考资料：\n{context}\n\n"
        "请基于以上资料回答用户的问题，若资料不足请明确说明。"
    ).format(context=context or "")
    return await _ask(chat, [SystemMessage(content=system), HumanMessage(content=q)])


async def _judge(chat, q: str, gold: str, evidence: str, answer: str) -> tuple[int, str]:
    try:
        raw = await _ask(chat, [HumanMessage(content=JUDGE_PROMPT.format(
            question=q, gold=gold, evidence=(evidence or "")[:2500], answer=(answer or "")[:2500]))])
        j = _extract_json(raw)
        return int(j.get("correct", 0)), str(j.get("reason", ""))[:150]
    except Exception:
        return 0, "judge error"


async def _sample_paths(uid: str, n: int, seed: int) -> list[dict]:
    d = get_neo4j_driver()
    r = await d.execute_query(
        "MATCH (a:Entity {user_id:$uid})-[r1:RELATES_TO]->(b:Entity {user_id:$uid})"
        "-[r2:RELATES_TO]->(c:Entity {user_id:$uid}) "
        "WHERE a.name<>b.name AND b.name<>c.name AND a.name<>c.name "
        "RETURN a.name AS a, r1.relation_type AS r1, b.name AS b, r2.relation_type AS r2, c.name AS c LIMIT 500",
        {"uid": uid},
    )
    paths = []
    seen = set()
    for rec in r.records:
        key = (rec["a"], rec["b"], rec["c"])
        if key in seen:
            continue
        seen.add(key)
        paths.append({"a": rec["a"], "r1": rec["r1"], "b": rec["b"], "r2": rec["r2"], "c": rec["c"]})
    rng = random.Random(seed)
    return rng.sample(paths, min(n, len(paths)))


def _evidence_text(evidences) -> str:
    parts = []
    for ev in evidences:
        parts.append(ev.content or "")
    return "\n".join(parts)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="eval-duretrieval")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--results-dir", type=Path, default=BACKEND / "results" / "eval_graph_multihop")
    args = ap.parse_args()

    paths = await _sample_paths(args.user_id, args.n, args.seed)
    print(f"[paths] sampled={len(paths)}")
    if not paths:
        print("图中没有 2 跳路径，退出")
        return

    chat = ChatModelFactory().generator()
    svc_graph = AgenticRagService(
        planner=FixedPlanner([RetrievalStep(tool="search_graph", query="", top_k=args.top_k)]),
        web_search_client=WebSearchClient(enabled=False),
    )
    svc_text = AgenticRagService(
        planner=FixedPlanner([RetrievalStep(tool="hybrid_search", query="", top_k=args.top_k)]),
        web_search_client=WebSearchClient(enabled=False),
    )

    rows = []
    g_ac = g_has_b = t_has_b = 0
    for i, p in enumerate(paths, 1):
        q = f"根据知识图谱，实体“{p['a']}”和“{p['c']}”之间是如何关联的？请指出中间实体与关系。"
        # 图路径
        t0 = time.time()
        rg = await svc_graph.run(q, args.user_id)
        tg = (time.time() - t0) * 1000
        g_ents = []
        for ev in rg.evidences:
            if ev.source == "graph" and ev.title and ev.title not in g_ents:
                g_ents.append(ev.title)
        g_ac_hit = p["a"] in g_ents and p["c"] in g_ents
        g_text = _evidence_text(rg.evidences)
        g_has_b_hit = p["b"] in g_text
        g_ac += int(g_ac_hit); g_has_b += int(g_has_b_hit)

        # 文本路径
        rt = await svc_text.run(q, args.user_id)
        t_text = _evidence_text(rt.evidences)
        t_has_b_hit = p["b"] in t_text
        t_has_b += int(t_has_b_hit)

        # 各自生成答案 + 裁判
        a_g = await _gen_answer(chat, rg.context, q)
        a_t = await _gen_answer(chat, rt.context, q)
        cg, rg_reason = await _judge(chat, q, p["b"], rg.context, a_g)
        ct, rt_reason = await _judge(chat, q, p["b"], rt.context, a_t)

        rows.append({
            "path": f"{p['a']} -{p['r1']}-> {p['b']} -{p['r2']}-> {p['c']}",
            "question": q, "gold_mid": p["b"],
            "graph_entities": g_ents, "graph_ac_hit": g_ac_hit, "graph_evidence_has_b": g_has_b_hit,
            "graph_answer_correct": cg, "graph_answer": a_g[:200], "graph_latency_ms": round(tg, 1),
            "text_evidence_has_b": t_has_b_hit, "text_answer_correct": ct,
        })
        print(f"[{i}/{len(paths)}] a/c命中(g={g_ac_hit}) 证据含b(g={g_has_b_hit},t={t_has_b_hit}) "
              f"ans(g={cg},t={ct}) | {p['a'][:8]}→{p['b'][:8]}→{p['c'][:8]}")

    n = len(rows)
    agg = {
        "n_paths": n,
        "graph_ac_entity_recall": round(g_ac / n, 3),
        "graph_evidence_has_b": round(g_has_b / n, 3),
        "text_evidence_has_b": round(t_has_b / n, 3),
        "graph_answer_accuracy": round(sum(r["graph_answer_correct"] for r in rows) / n, 3),
        "text_answer_accuracy": round(sum(r["text_answer_correct"] for r in rows) / n, 3),
        "delta_answer": round((sum(r["graph_answer_correct"] for r in rows) - sum(r["text_answer_correct"] for r in rows)) / n, 3),
        "mean_graph_latency_ms": round(statistics.mean(r["graph_latency_ms"] for r in rows), 1),
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "report.json").write_text(
        json.dumps({"summary": agg, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 多跳问答：GraphRAG vs 纯文本检索增量评测", "",
          f"- 用户：`{args.user_id}`  2 跳路径样本：{n}",
          "- 口径：题目从图中真实路径生成；gold 中间实体=b；当前图检索为「实体匹配+chunk 提及」，不做邻居扩展",
          "", "| 指标 | 图检索(GraphRAG) | 纯文本检索 |", "|---|---|---|",
          f"| 两端实体 a/c 命中 | {agg['graph_ac_entity_recall']:.3f} | — |",
          f"| 检索证据含中间实体 b | {agg['graph_evidence_has_b']:.3f} | {agg['text_evidence_has_b']:.3f} |",
          f"| 端到端答案正确率 | {agg['graph_answer_accuracy']:.3f} | {agg['text_answer_accuracy']:.3f} |",
          f"| 答案正确率增量 | +{agg['delta_answer']:.3f} | — |", "",
          f"- 图路径检索延迟均值：{agg['mean_graph_latency_ms']:.0f} ms", "",
          "## 逐路径明细", "", "| 路径 | a/c命中 | 证据含b(G/T) | 答案(G/T) |", "|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['path'][:48]} | {int(r['graph_ac_hit'])} | {int(r['graph_evidence_has_b'])}/{int(r['text_evidence_has_b'])} | {r['graph_answer_correct']}/{r['text_answer_correct']} |")
    (args.results_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"[done] -> {args.results_dir / 'report.json'}")


if __name__ == "__main__":
    asyncio.run(main())
