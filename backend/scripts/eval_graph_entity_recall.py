"""实体级 GraphRAG 检索评测：实体 hit@k / recall@k / MRR。

对每条样本：取图中一个实体 E，构造问题“介绍一下{E}”，走真实图谱检索路径：
    LocalRetriever.search(user_id, [RetrievalStep(tool="search_graph", query=q, top_k=10)])
    → LLM 从问题抽实体候选 → search_entities 模糊匹配 → 返回实体证据（source="graph"）
gold = {E}，按返回的实体证据顺序（去重）算 hit@k / MRR / recall@k。

⚠️ 口径：评测的是「图谱检索路径」（实体抽取→匹配），题目从实体名生成，检索难度偏易；
体现的是图检索正确性/覆盖面，不是对抗性难度。回答质量/多跳推理另见 eval_graph_multihop.py。

读：Neo4j（--user-id）
写：results/eval_graph_entity/report.json + report.md
"""
import argparse
import asyncio
import json
import math
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


def _ranked_entity_names(evidences) -> list[str]:
    """只取 source='graph' 的实体证据，按返回顺序去重得实体名列表。"""
    names: list[str] = []
    for ev in evidences:
        if ev.source != "graph":
            continue
        name = (ev.title or "").strip()
        if name and name not in names:
            names.append(name)
    return names


async def _all_entity_names(uid: str) -> list[str]:
    d = get_neo4j_driver()
    r = await d.execute_query(
        "MATCH (e:Entity {user_id:$uid}) RETURN e.name AS n",
        {"uid": uid},
    )
    names = []
    for rec in r.records:
        n = (rec["n"] or "").strip()
        if len(n) >= 2:
            names.append(n)
    return list(dict.fromkeys(names))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="eval-duretrieval")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--results-dir", type=Path, default=BACKEND / "results" / "eval_graph_entity")
    ap.add_argument("--warmup", type=int, default=2)
    args = ap.parse_args()

    names = await _all_entity_names(args.user_id)
    rng = random.Random(args.seed)
    sampled = rng.sample(names, min(args.n, len(names)))
    print(f"[data] entities={len(names)} sampled={len(sampled)}")

    retriever = LocalRetriever()
    for _ in range(args.warmup):
        await retriever.search(args.user_id, [RetrievalStep(tool="search_graph", query="预热 张万诚", top_k=3)])

    rows = []
    lat = []
    for i, name in enumerate(sampled, 1):
        q = f"介绍一下{name}"
        t0 = time.time()
        try:
            evs = await retriever.search(
                args.user_id, [RetrievalStep(tool="search_graph", query=q, top_k=args.top_k)]
            )
        except Exception as e:
            print(f"[{i}] search_graph 异常 {name}: {e}")
            rows.append({"entity": name, "query": q, "ranked": [], "hit@1": 0, "hit@3": 0, "hit@5": 0, "hit@10": 0, "mrr": 0.0})
            continue
        dt = (time.time() - t0) * 1000
        lat.append(dt)
        ranked = _ranked_entity_names(evs)
        gt = {name}
        r = {"entity": name, "query": q, "ranked": ranked, "rank": None}
        for pos, n in enumerate(ranked, 1):
            if n in gt:
                r["rank"] = pos
                break
        rank = r["rank"]
        for k in TOP_Ks:
            r[f"hit@{k}"] = 1.0 if rank is not None and rank <= k else 0.0
        r["mrr"] = 1.0 / rank if rank else 0.0
        rows.append(r)
        print(f"[{i}/{len(sampled)}] {name[:20]} rank={rank} n_entities={len(ranked)} t={dt:.0f}ms")

    n = max(1, len(rows))
    agg = {f"hit@{k}": round(sum(r[f"hit@{k}"] for r in rows) / n, 4) for k in TOP_Ks}
    agg["mrr"] = round(statistics.mean(r["mrr"] for r in rows), 4)
    agg["avg_rank"] = round(statistics.mean([r["rank"] for r in rows if r["rank"]]), 2)
    agg["retrieved_none"] = sum(1 for r in rows if not r["ranked"])
    agg["latency_ms_p50"] = round(statistics.median(lat), 1) if lat else 0

    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "report.json").write_text(
        json.dumps({"summary": agg, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 实体级 GraphRAG 检索评测", "",
          f"- 用户：`{args.user_id}`  样本：{len(rows)} 实体",
          f"- 口径：从图中取实体构造问题，测真实 search_graph 路径（实体抽取→匹配）",
          "", "| 指标 | 值 |", "|---|---|"]
    for k in TOP_Ks:
        md.append(f"| 实体 hit@{k} | {agg[f'hit@{k}']:.4f} |")
    md.append(f"| 实体 MRR | {agg['mrr']:.4f} |")
    md.append(f"| 平均命中位次 | {agg['avg_rank']} |")
    md.append(f"| 未召回任何实体 | {agg['retrieved_none']} |")
    md.append(f"| 检索延迟 p50 | {agg['latency_ms_p50']} ms |")
    (args.results_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"[done] -> {args.results_dir / 'report.json'}")


if __name__ == "__main__":
    asyncio.run(main())
