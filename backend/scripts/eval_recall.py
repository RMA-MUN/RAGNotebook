"""按 manifest 做检索信息量评测（标准 IR 口径），并记录每 query 延迟。

对每条 query 走真实检索原语：
    query_embedding = embed_model.embed_query(q)
    hits = Neo4jGraphStore.search_chunks(user_id, emb, q, kinds=["doc"], limit=--limit)
按 chunk 的 source_id 去重映射回文档，得到文档级排序，再对齐 manifest 的相关文档算指标。

指标（相关性 qrels 为二元，score=1）
- recall@k：top-k 内相关文档占比
- hit@k：top-k 内是否至少命中一条相关文档
- MRR：首个相关文档的倒数排名（@limit 截断）
- nDCG@10：DCG@10 / IDCG@10（标准排序质量）
- MAP@limit：平均精度（分母=该 query 相关文档总数）

读：results/eval_retrieval/manifest.json（或 --manifest 指定）
写：results/eval_retrieval/report.json + report.md（或 --results-dir 指定）
"""
import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.graph.storage.neo4j_graph_store import Neo4jGraphStore  # noqa: E402
from app.utils.factory import EmbedModelFactory  # noqa: E402

TOP_Ks = [1, 3, 5, 10]
NDCG_K = 10


def _ranked_doc_ids(hits) -> list[str]:
    seen: list[str] = []
    for h in hits:
        sid = h.source_id
        if sid and sid not in seen:
            seen.append(sid)
    return seen


def _ndcg_at(ranked: list[str], gt: set[str], k: int = NDCG_K) -> float:
    dcg = 0.0
    for i, doc in enumerate(ranked[:k], 1):
        if doc in gt:
            dcg += 1.0 / math.log2(i + 1)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(k, len(gt)) + 1))
    return dcg / idcg if idcg else 0.0


def _map(ranked: list[str], gt: set[str]) -> float:
    """MAP：平均精度，分母为该 query 的相关文档总数（未检索回来的计为漏检）。"""
    if not gt:
        return 0.0
    hits = 0
    prec_sum = 0.0
    for i, doc in enumerate(ranked, 1):
        if doc in gt:
            hits += 1
            prec_sum += hits / i
    return prec_sum / len(gt)


def _metrics_for(ranked: list[str], gt: set[str]) -> dict:
    rel_total = len(gt)
    rel_dict = {doc: i for i, doc in enumerate(ranked, 1) if doc in gt}
    mrr = 0.0
    if rel_dict:
        mrr = 1.0 / min(rel_dict.values())
    out = {}
    for k in TOP_Ks:
        top = set(ranked[:k])
        hit = any(top & gt)
        recall = len(top & gt) / rel_total if rel_total else 0.0
        out[f"recall@{k}"] = recall
        out[f"hit@{k}"] = 1.0 if hit else 0.0
    out["mrr"] = mrr
    out["ndcg@10"] = _ndcg_at(ranked, gt, NDCG_K)
    out["map"] = _map(ranked, gt)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=BACKEND / "results" / "eval_retrieval" / "manifest.json")
    ap.add_argument("--results-dir", type=Path, default=BACKEND / "results" / "eval_retrieval")
    ap.add_argument("--limit", type=int, default=100, help="检索池/文档级截断；MAP 以该值为上限")
    ap.add_argument("--warmup", type=int, default=1, help="跑 N 条占位 query 预热索引后再计时")
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text(encoding="utf-8"))
    user_id = m["user_id"]
    query_text = m["query_text"]
    relevant = m["relevant_docs"]
    qids = m["query_ids"]

    store = Neo4jGraphStore()
    model = EmbedModelFactory().generator()

    async def search(q: str):
        emb = await asyncio.to_thread(model.embed_query, q)
        hits = await store.search_chunks(user_id, emb, q, ["doc"], args.limit)
        return _ranked_doc_ids(hits)

    # 预热
    for _ in range(args.warmup):
        await search(qids[0])

    rows = []
    latencies = []
    for qid in qids:
        qtext = query_text.get(qid, "")
        gt = set(relevant.get(qid, []))
        t0 = time.time()
        ranked = await search(qtext)
        dt = (time.time() - t0) * 1000.0
        latencies.append(dt)
        metrics = _metrics_for(ranked, gt)
        rows.append({"query_id": qid, "query": qtext, "latency_ms": round(dt, 1),
                     "top_doc_ids": ranked, "relevant_count": len(gt), **metrics})

    agg = {f"recall@{k}": round(sum(r[f"recall@{k}"] for r in rows) / len(rows), 4) for k in TOP_Ks}
    agg.update({f"hit@{k}": round(sum(r[f"hit@{k}"] for r in rows) / len(rows), 4) for k in TOP_Ks})
    agg["mrr"] = round(statistics.mean(r["mrr"] for r in rows), 4)
    agg["ndcg@10"] = round(statistics.mean(r["ndcg@10"] for r in rows), 4)
    agg["map"] = round(statistics.mean(r["map"] for r in rows), 4)
    lat = statistics.median(latencies)
    agg["latency_ms"] = {
        "p50": round(lat, 1),
        "p90": round(sorted(latencies)[int(len(latencies) * 0.9) - 1], 1),
        "p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1),
        "mean": round(statistics.mean(latencies), 1),
        "max": round(max(latencies), 1),
    }
    summary = {
        "user_id": user_id,
        "n_queries": len(rows),
        "n_docs": len(m["doc_ids"]),
        "total_chunks": m.get("total_chunks"),
        "source_dataset": m.get("source_dataset"),
        "aggregate": agg,
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "report.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 检索信息量评测报告", ""]
    md.append(f"- 数据集：{m.get('source_dataset')}  (queries={len(qids)}, docs={len(m['doc_ids'])}, chunks={m.get('total_chunks')})")
    md.append(f"- 评测用户：`{user_id}`")
    md.append("- 指标：recall@k（相关文档被召回占比）、hit@k（至少召回一条相关文档）、MRR（首个相关文档的倒数排名）、nDCG@10（排序质量）、MAP（平均精度）")
    md.append("")
    md.append("| 指标 | 值 |")
    md.append("|---|---|")
    for k in TOP_Ks:
        md.append(f"| recall@{k} | {agg[f'recall@{k}']:.4f} |")
    for k in TOP_Ks:
        md.append(f"| hit@{k} | {agg[f'hit@{k}']:.4f} |")
    md.append(f"| MRR | {agg['mrr']:.4f} |")
    md.append(f"| nDCG@10 | {agg['ndcg@10']:.4f} |")
    md.append(f"| MAP | {agg['map']:.4f} |")
    md.append("")
    md.append("## 检索延迟（src→top-k 结果，每 query 计时）")
    md.append("")
    md.append("| 分位 | 值(ms) |")
    md.append("|---|---|")
    for k in ("p50", "p90", "p95", "mean", "max"):
        md.append(f"| {k} | {agg['latency_ms'][k]:.1f} |")
    md.append("")
    md.append("## 逐 query 明细（前 30 条）")
    md.append("")
    md.append("| query_id | 相关文档数 | recall@5 | hit@5 | nDCG@10 | MAP | MRR | 延迟(ms) |")
    md.append("|---|---|---|---|---|---|---|---|")
    for r in rows[:30]:
        md.append(f"| {r['query_id'][:10]} | {r['relevant_count']} | {r['recall@5']:.3f} | {r['hit@5']:.1f} | {r['ndcg@10']:.3f} | {r['map']:.3f} | {r['mrr']:.3f} | {r['latency_ms']} |")
    (args.results_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[done] -> {args.results_dir / 'report.json'}")
    print(f"[done] -> {args.results_dir / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
