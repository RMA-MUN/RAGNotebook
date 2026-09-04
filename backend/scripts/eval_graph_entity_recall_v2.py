"""实体级 GraphRAG 检索评测 v2：用「关系线索题」测真实召回（答案实体不出现在问题中）。

v1 的教训：把实体名写进问题（“介绍一下{X}”）→ 子串匹配恒 rank1 → hit@k 不随 k 变化，
退化成“名字查字典”，无区分度。v2 改为：

- 从 RELATES_TO 边采样：a -[rel]-> b
- 题目只含 a + rel，gold = b（问题里没有 b）：
    “在知识图谱中，与实体「a」存在「rel」关系的实体是哪一个？”
- 走真实 search_graph 检索路径，测：
    - 答案实体（b）作为**实体节点**被召回的 hit@k / MRR
    - 答案实体（b）出现在**检索证据文本**里的覆盖率（图检索只能靠 chunk 文本暴露 b）

这样 hit@1/3/5 会随 k 变化、数字有区分度，才反映真实图谱检索能力。

读：Neo4j（--user-id）
写：results/eval_graph_entity/report_v2.json + report_v2.md
"""
import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.graph.storage.neo4j_client import get_neo4j_driver  # noqa: E402
from app.rag.agentic_rag.local_retriever import LocalRetriever  # noqa: E402
from app.rag.agentic_rag.schemas import RetrievalStep  # noqa: E402

TOP_Ks = [1, 3, 5, 10]
QUERY_TEMPLATE = "在知识图谱中，与实体「{a}」存在「{rel}」关系的实体是哪一个？"


def _ranked_entity_names(evidences) -> list[str]:
    names: list[str] = []
    for ev in evidences:
        if ev.source != "graph":
            continue
        name = (ev.title or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _evidence_text(evidences) -> str:
    return "\n".join(ev.content or "" for ev in evidences)


async def _sample_edges(uid: str, n: int, seed: int) -> list[dict]:
    d = get_neo4j_driver()
    r = await d.execute_query(
        "MATCH (a:Entity {user_id:$uid})-[rel:RELATES_TO]->(b:Entity {user_id:$uid}) "
        "WHERE a.name <> b.name AND size(rel.relation_type) >= 2 "
        "RETURN a.name AS a, rel.relation_type AS rel, b.name AS b LIMIT 800",
        {"uid": uid},
    )
    edges = []
    seen = set()
    for rec in r.records:
        key = (rec["a"], rec["rel"], rec["b"])
        if key in seen:
            continue
        seen.add(key)
        edges.append({"a": rec["a"], "rel": rec["rel"], "b": rec["b"]})
    rng = random.Random(seed)
    return rng.sample(edges, min(n, len(edges)))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="eval-duretrieval")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--results-dir", type=Path, default=BACKEND / "results" / "eval_graph_entity")
    ap.add_argument("--warmup", type=int, default=2)
    args = ap.parse_args()

    edges = await _sample_edges(args.user_id, args.n, args.seed)
    print(f"[edges] sampled={len(edges)}")

    retriever = LocalRetriever()
    for _ in range(args.warmup):
        await retriever.search(args.user_id, [RetrievalStep(tool="search_graph", query="预热 张万诚 关联", top_k=3)])

    rows = []
    lat = []
    for i, e in enumerate(edges, 1):
        q = QUERY_TEMPLATE.format(a=e["a"], rel=e["rel"])
        t0 = time.time()
        try:
            evs = await retriever.search(
                args.user_id, [RetrievalStep(tool="search_graph", query=q, top_k=args.top_k)]
            )
        except Exception as exc:
            print(f"[{i}] 异常 {e['a']}->{e['b']}: {exc}")
            continue
        dt = (time.time() - t0) * 1000
        lat.append(dt)
        ranked = _ranked_entity_names(evs)
        gold = e["b"]
        rank = next((pos for pos, nm in enumerate(ranked, 1) if nm == gold), None)
        r = {"query": q, "a": e["a"], "rel": e["rel"], "gold": gold,
             "ranked": ranked, "rank": rank,
             "answer_in_evidence_text": gold in _evidence_text(evs)}
        for k in TOP_Ks:
            r[f"hit@{k}"] = 1.0 if rank is not None and rank <= k else 0.0
        r["mrr"] = 1.0 / rank if rank else 0.0
        rows.append(r)
        print(f"[{i}/{len(edges)}] gold={gold[:12]} rank={rank} n_ent={len(ranked)} "
              f"in_text={r['answer_in_evidence_text']} t={dt:.0f}ms")

    n = max(1, len(rows))
    agg = {f"hit@{k}": round(sum(r[f"hit@{k}"] for r in rows) / n, 4) for k in TOP_Ks}
    agg["mrr"] = round(statistics.mean(r["mrr"] for r in rows), 4)
    agg["answer_in_evidence_text"] = round(
        sum(r["answer_in_evidence_text"] for r in rows) / n, 4)
    agg["retrieved_none"] = sum(1 for r in rows if not r["ranked"])
    agg["avg_rank"] = round(statistics.mean([r["rank"] for r in rows if r["rank"]]), 2)
    agg["latency_ms_p50"] = round(statistics.median(lat), 1) if lat else 0

    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "report_v2.json").write_text(
        json.dumps({"summary": agg, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 实体级 GraphRAG 检索评测 v2（关系线索题）", "",
          f"- 用户：`{args.user_id}`  样本：{len(rows)} 条关系边",
          "- 口径：题目只含实体 a + 关系 rel，gold=答案实体 b（问题中不含 b）",
          "", "| 指标 | 值 |", "|---|---|"]
    for k in TOP_Ks:
        md.append(f"| 答案实体 hit@{k} | {agg[f'hit@{k}']:.4f} |")
    md.append(f"| 答案实体 MRR | {agg['mrr']:.4f} |")
    md.append(f"| 答案实体出现在证据文本 | {agg['answer_in_evidence_text']:.4f} |")
    md.append(f"| 平均命中位次 | {agg['avg_rank']} |")
    md.append(f"| 未召回任何实体 | {agg['retrieved_none']} |")
    md.append(f"| 检索延迟 p50 | {agg['latency_ms_p50']} ms |")
    (args.results_dir / "report_v2.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"[done] -> {args.results_dir / 'report_v2.json'}")


if __name__ == "__main__":
    asyncio.run(main())
