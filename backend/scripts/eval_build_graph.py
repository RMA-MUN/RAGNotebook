"""给评测语料建知识图谱（实体/关系/MENTIONS），绕过 MySQL 任务表直接写 Neo4j。

流程：
1. 从 manifest 抽 doc_ids、从 corpus 取正文，随机采样 n_docs 篇；
2. 并行（--concurrency）调用 LLM 抽取实体+关系（extract_entities）；
3. 顺序写 Neo4j：upsert_entity（按名去重）→ create_relation（同源实体间）→
   set_source_mentions / set_chunk_mentions（Doc/Chunk→Entity MENTIONS 边，规则匹配零 LLM 成本）。

隔离：固定 eval-graph 用户；--clean 先清空该用户实体/关系再重建。
产出：results/eval_graph/report.json（实体/关系/边统计）。
"""
import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.graph.extraction.chunk_matcher import match_entities_in_chunks  # noqa: E402
from app.graph.extraction.entity_extractor import extract_entities  # noqa: E402
from app.graph.schemas.graph import EntityIn, RelationIn  # noqa: E402
from app.graph.storage.neo4j_client import get_neo4j_driver  # noqa: E402
from app.graph.storage.neo4j_graph_store import Neo4jGraphStore  # noqa: E402
from app.utils.factory import ChatModelFactory  # noqa: E402

DEFAULT_USER = "eval-duretrieval"


async def _doc_chunks(uid: str, doc_id: str) -> list[str]:
    """按 chunk_index 顺序取该文档的全部 chunk 正文。"""
    driver = get_neo4j_driver()
    r = await driver.execute_query(
        "MATCH (c:Chunk {kind:'doc', user_id:$uid, source_id:$sid}) "
        "RETURN c.chunk_index AS i, c.text AS t ORDER BY c.chunk_index",
        {"uid": uid, "sid": doc_id},
    )
    return [rec["t"] or "" for rec in r.records]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=BACKEND / "results" / "eval_retrieval" / "manifest.json")
    ap.add_argument("--corpus", type=Path, default=BACKEND / "data" / "eval" / "duretrieval" / "corpus.parquet")
    ap.add_argument("--results-dir", type=Path, default=BACKEND / "results" / "eval_graph")
    ap.add_argument("--user-id", default=DEFAULT_USER)
    ap.add_argument("--n-docs", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--clean", action="store_true", help="先清空 eval-graph 用户全部实体/关系/MENTIONS")
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text(encoding="utf-8"))
    doc_ids_all = set(m["doc_ids"])
    corpus = pd.read_parquet(args.corpus)
    ctext = dict(zip(corpus["_id"].astype(str), corpus["text"].astype(str)))
    pool = [d for d in ctext if d in doc_ids_all and ctext[d].strip()]
    rng = random.Random(args.seed)
    docs = rng.sample(pool, min(args.n_docs, len(pool)))
    print(f"[sample] docs={len(docs)}")

    store = Neo4jGraphStore()
    if args.clean:
        # 清空该用户的实体/关系及关联边（不动 Doc/Chunk）
        driver = get_neo4j_driver()
        await driver.execute_query(
            "MATCH (e:Entity {user_id:$uid}) DETACH DELETE e",
            {"uid": args.user_id},
        )
        print("[clean] 已清空 eval-graph 实体/关系")

    chat = ChatModelFactory().generator()
    sem = asyncio.Semaphore(args.concurrency)

    async def extract_one(doc_id: str):
        async with sem:
            res = await extract_entities("", ctext[doc_id][:6000], chat)
            return doc_id, res

    # Phase 1: 并行抽取
    started = time.time()
    results = await asyncio.gather(*(extract_one(d) for d in docs))
    print(f"[extract] {len(results)} docs done in {time.time()-started:.0f}s")

    # Phase 2: 顺序写库
    ent_total = rel_total = src_mention = chunk_mention = 0
    for i, (doc_id, res) in enumerate(results, 1):
        name2id: dict[str, str] = {}
        for e in res.entities:
            name = (e.name or "").strip()
            if not name:
                continue
            try:
                ent = await store.upsert_entity(
                    args.user_id,
                    EntityIn(name=name, display_name=name,
                             description=(e.description or "")[:1000] or None,
                             aliases=[a for a in (e.aliases or []) if a and a != name][:10],
                             source_note_ids=[doc_id]),
                )
                name2id[name] = ent.id
            except Exception as exc:
                print(f"[warn] upsert_entity {name}: {exc}")
        rel_created = 0
        for r in res.relations:
            s, t = (r.source or "").strip(), (r.target or "").strip()
            if s in name2id and t in name2id and (r.relation_type or "").strip():
                try:
                    await store.create_relation(
                        args.user_id,
                        RelationIn(source_id=name2id[s], target_id=name2id[t],
                                   relation_type=r.relation_type.strip(), confidence=0.7),
                    )
                    rel_created += 1
                except Exception:
                    pass
        # MENTIONS：Doc 源级 + Chunk 级
        chunks = await _doc_chunks(args.user_id, doc_id)
        if chunks:
            matched = match_entities_in_chunks(res.entities, chunks)
            src_links = []
            chunk_links = []
            for name, idxs in matched.items():
                eid = name2id.get(name)
                if not eid:
                    continue
                src_links.append({"entity_id": eid, "mention_count": len(idxs), "context": []})
                chunk_links.append({"entity_id": eid, "chunk_indexes": idxs})
            if src_links:
                await store.set_source_mentions(args.user_id, "doc", doc_id, src_links)
                src_mention += len(src_links)
            if chunk_links:
                await store.set_chunk_mentions(args.user_id, "doc", doc_id, chunk_links)
                chunk_mention += len(chunk_links)
        ent_total += len(name2id)
        rel_total += rel_created
        if i % 25 == 0 or i == len(results):
            print(f"[write] {i}/{len(results)} entities={ent_total} relations={rel_total} ({time.time()-started:.0f}s)")

    # 统计
    driver = get_neo4j_driver()
    cnt = await driver.execute_query(
        "MATCH (e:Entity {user_id:$uid}) RETURN count(e) AS c", {"uid": args.user_id}
    )
    entity_nodes = cnt.records[0]["c"]
    cnt = await driver.execute_query(
        "MATCH ()-[r:RELATES_TO {user_id:$uid}]->() RETURN count(r) AS c", {"uid": args.user_id}
    )
    relation_edges = cnt.records[0]["c"]
    cnt = await driver.execute_query(
        "MATCH ()-[r:MENTIONS]->(:Entity {user_id:$uid}) RETURN count(r) AS c", {"uid": args.user_id}
    )
    mention_edges = cnt.records[0]["c"]

    summary = {
        "user_id": args.user_id,
        "docs_processed": len(docs),
        "extract_time_s": round(time.time() - started, 1),
        "entity_nodes": entity_nodes,
        "relation_edges": relation_edges,
        "mentions_edges": mention_edges,
        "docs_with_entities": ent_total,
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[done] -> {args.results_dir / 'report.json'}")


if __name__ == "__main__":
    asyncio.run(main())
