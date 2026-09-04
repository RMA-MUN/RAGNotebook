"""端到端 Agentic RAG 评测（A：答案质量 + B：延迟），基于已入库语料自动合成 QA。

流程（每条样本）：
1. 从语料取一段文档正文，LLM 自动生成 (question, reference_answer) —— 零手写。
2. AgenticRagService.run(question, user_id) 跑真实管线（规划+检索+可答性+证据融合）。
3. 用产品同款 system prompt（带 rag_context）+ question，让 chat 模型生成回答；用流式计时
   统计「首 token 时间」（思考→首token）。
4. LLM 裁判对 (question, ref_answer, evidence, answer) 打分：faithfulness(1-5)、correctness(1-5)。

⚠️ 口径提醒：题目由源正文生成，检索必然命中 → 本评测衡量「答案质量/忠实度/延迟」，
不衡量「检索难度」（检索难度用 DuRetrieval qrels 那套 recall/nDCG/MAP）。

读：results/eval_retrieval/manifest.json（拿 user_id + 已入库 doc_ids）
     data/eval/duretrieval/corpus.parquet（源正文）
写：results/eval_end2end/report.json + report.md
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
import pandas as pd  # noqa: E402

from app.rag.agentic_rag.service import AgenticRagService  # noqa: E402
from app.utils.factory import ChatModelFactory  # noqa: E402

GEN_PROMPT = (
    "你是评测数据生成器。根据下面这段文档，生成一个真实用户会提出的问题，并给出一段准确、"
    "完整的参考答案（可忠实引用原文）。只返回 JSON（不要任何多余文字）：\n"
    '{{"question": "...", "answer": "..."}}\n\n文档：\n{doc}'
)

JUDGE_PROMPT = (
    "你是严格的 RAG 评测裁判。判断【回答】在多大程度上由【检索证据】支撑（忠实度 faithfulness），"
    "以及它是否正确回答了【问题】（正确性 correctness，以【参考答案】为准）。\n"
    "打分 1-5：5=完全支撑/完全正确，3=部分，1=基本不支撑/错误。\n"
    '只返回 JSON：{{"faithfulness": <1-5>, "correctness": <1-5>, "faithful_reason": "...", "correct_reason": "..."}}\n\n'
    "问题：{question}\n参考答案：{ref_answer}\n检索证据：{evidence}\n回答：{answer}"
)

ANSWER_SYSTEM = (
    "你是用户的智能助手。\n\n以下是与用户问题相关的参考资料：\n{context}\n\n"
    "请基于以上资料回答用户的问题。回答时必须区分本地证据（笔记、知识库）与外部搜索证据，"
    "避免把外部搜索内容说成用户本地资料。如果资料中没有足够信息支撑结论，必须明确说明证据不足，"
    "并说明还缺少哪些信息。"
)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in: {text[:200]}")
    return json.loads(m.group(0))


def _as_int(v) -> int:
    try:
        return max(1, min(5, int(float(v))))
    except (TypeError, ValueError):
        return 1


async def _ask(chat, messages) -> str:
    resp = await chat.ainvoke(messages)
    content = getattr(resp, "content", resp)
    return content if isinstance(content, str) else str(content)


async def _stream_answer(chat, system_prompt: str, question: str):
    """流式生成完整回答，返回 (full_text, first_token_ms)。"""
    full: list[str] = []
    first_ms = None
    t0 = time.time()
    async for chunk in chat.astream([SystemMessage(content=system_prompt), HumanMessage(content=question)]):
        text = getattr(chunk, "content", chunk)
        if not text:
            continue
        if first_ms is None:
            first_ms = (time.time() - t0) * 1000.0
        if isinstance(text, list):
            text = "".join(c.get("text", "") for c in text if isinstance(c, dict))
        full.append(text)
    return "".join(full), first_ms


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=BACKEND / "results" / "eval_retrieval" / "manifest.json")
    ap.add_argument("--corpus", type=Path, default=BACKEND / "data" / "eval" / "duretrieval" / "corpus.parquet")
    ap.add_argument("--results-dir", type=Path, default=BACKEND / "results" / "eval_end2end")
    ap.add_argument("--n-samples", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--max-evidence-chars", type=int, default=3000)
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text(encoding="utf-8"))
    user_id = m["user_id"]
    doc_ids = set(m["doc_ids"])

    corpus = pd.read_parquet(args.corpus)
    ctext = dict(zip(corpus["_id"].astype(str), corpus["text"].astype(str)))
    pool = [d for d in ctext if d in doc_ids and ctext[d].strip()]
    rng = random.Random(args.seed)
    sample_docs = rng.sample(pool, min(args.n_samples, len(pool)))
    print(f"[data] user={user_id} sample_docs={len(sample_docs)}")

    chat = ChatModelFactory().generator()
    service = AgenticRagService()

    # 预热
    if args.warmup:
        await service.run("预热预取 query", user_id)

    rows = []
    skipped = 0
    for i, doc_id in enumerate(sample_docs, 1):
        doc = ctext[doc_id][:2500]
        try:
            gen_raw = await _ask(chat, [HumanMessage(content=GEN_PROMPT.format(doc=doc))])
            qa = _extract_json(gen_raw)
            q = (qa.get("question") or "").strip()
            ref = (qa.get("answer") or "").strip()
        except Exception as e:
            print(f"[{i}] QA 生成失败, skip: {e}")
            skipped += 1
            continue
        if not q or not ref:
            skipped += 1
            continue

        t0 = time.time()
        try:
            result = await service.run(q, user_id)
        except Exception as e:
            print(f"[{i}] AgenticRAG 失败, skip: {e}")
            skipped += 1
            continue
        rag_ms = (time.time() - t0) * 1000.0
        evidence = (result.context or "")[: args.max_evidence_chars]
        ev_count = len(result.evidences)

        answer, first_ms = await _stream_answer(chat, ANSWER_SYSTEM.format(context=result.context or ""), q)
        if not answer.strip():
            answer = answer or "(empty)"

        try:
            jraw = await _ask(chat, [HumanMessage(content=JUDGE_PROMPT.format(
                question=q, ref_answer=ref[:2000], evidence=evidence, answer=answer[:3000]))])
            j = _extract_json(jraw)
            faithfulness = _as_int(j.get("faithfulness"))
            correctness = _as_int(j.get("correctness"))
            f_reason = str(j.get("faithful_reason") or "")[:200]
            c_reason = str(j.get("correct_reason") or "")[:200]
        except Exception as e:
            print(f"[{i}] 裁判失败, set neutral: {e}")
            faithfulness = correctness = 3
            f_reason = c_reason = "judge parse error"

        rows.append({
            "doc_id": doc_id, "question": q, "reference_answer": ref, "answer": answer,
            "evidence_count": ev_count, "faithfulness": faithfulness, "correctness": correctness,
            "faithful_reason": f_reason, "correct_reason": c_reason,
            "rag_ms": round(rag_ms, 1), "answer_first_token_ms": round(first_ms, 1) if first_ms else None,
        })
        print(f"[{i}/{len(sample_docs)}] fa={faithfulness} corr={correctness} ev={ev_count} rag={rag_ms:.0f}ms first={first_ms:.0f}ms")
        await asyncio.sleep(0.3)  # 缓和rate limit

    n = max(1, len(rows))
    agg = {
        "n_samples": len(rows),
        "n_skipped": skipped,
        "faithfulness_mean": round(sum(r["faithfulness"] for r in rows) / n, 3),
        "correctness_mean": round(sum(r["correctness"] for r in rows) / n, 3),
        "faithfulness_rate_4plus": round(sum(1 for r in rows if r["faithfulness"] >= 4) / n, 3),
        "correctness_rate_4plus": round(sum(1 for r in rows if r["correctness"] >= 4) / n, 3),
        "avg_evidence_count": round(sum(r["evidence_count"] for r in rows) / n, 2),
        "mean_rag_ms": round(statistics.mean(r["rag_ms"] for r in rows), 1),
        "mean_answer_first_token_ms": round(statistics.mean(
            r["answer_first_token_ms"] for r in rows if r["answer_first_token_ms"] is not None), 1),
        "estimated_total": round(statistics.mean(
            (r["rag_ms"] + (r["answer_first_token_ms"] or 0)) for r in rows), 1),
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "report.json").write_text(json.dumps({"summary": agg, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Agentic RAG 端到端评测报告（自动合成 QA）", ""]
    md.append(f"- 评测用户：`{user_id}`  样本：{len(rows)}（跳过 {skipped}）")
    md.append(f"- 口径：题目从源正文自动生成，衡量**答案质量/忠实度/延迟**，**不衡量检索难度**")
    md.append("")
    md.append("| 指标 | 值 |")
    md.append("|---|---|")
    md.append(f"| 忠实度(1-5) | {agg['faithfulness_mean']:.2f} |")
    md.append(f"| 正确性(1-5) | {agg['correctness_mean']:.2f} |")
    md.append(f"| 忠实度≥4 占比 | {agg['faithfulness_rate_4plus']:.3f} |")
    md.append(f"| 正确性≥4 占比 | {agg['correctness_rate_4plus']:.3f} |")
    md.append(f"| 平均证据条数 | {agg['avg_evidence_count']:.2f} |")
    md.append("")
    md.append("## 延迟（思考/检索 → 首token）")
    md.append("")
    md.append("| 项 | 值(ms) |")
    md.append("|---|---|")
    md.append(f"| 管线(规划+检索+可答性) | {agg['mean_rag_ms']:.1f} |")
    md.append(f"| 回答首token | {agg['mean_answer_first_token_ms']:.1f} |")
    md.append(f"| 用户感知首token ≈ | {agg['estimated_total']:.1f} |")
    md.append("")
    md.append("> 说明：本脚本用 stream 估算回答首token（管线思考后→首个字符）。真实 /chat/agent/query/stream 含 Agent 工具轮，建议另测。")
    (args.results_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"[done] -> {args.results_dir / 'report.json'} / report.md")


if __name__ == "__main__":
    asyncio.run(main())
