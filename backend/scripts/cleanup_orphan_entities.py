"""清理 Neo4j 图谱中的零度孤立实体（一次性维护脚本）。

背景：笔记多次抽取时 LLM 产出的实体名不稳定（《琵琶行》 vs 琵琶行），
upsert 的精确名/别名去重无法命中，重抽重建 MENTIONS 后旧节点被拔成孤儿。

处理策略（逐个零度 Entity）：
- 分组内存在连通孪生（去《》后同名）→ 孤儿属性并入孪生（aliases 并集、
  confidence 取 max、description 兜底、source_note_ids 并集）后删除孤儿；
- 组内全为零度 → 留一个作为幸存者，按其 source_note_ids 恢复
  (Note)-[:MENTIONS]->(幸存者) 边（属性与 set_source_mentions 同款），
  让画布重新挂边；笔记不存在则跳过。

用法（在 backend 目录下执行）：
  uv run python -m scripts.cleanup_orphan_entities --dry-run   # 只打印计划，不写库
  uv run python -m scripts.cleanup_orphan_entities             # 执行清理
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neo4j import GraphDatabase

from app.core.failed_response import settings
from app.core.logger_handler import logger

# 实体名归一化：去书名号与空白（LLM 产出风格不稳定的已知差异面）
_NORMALIZE_RE = re.compile(r"[《》\s]+")


def _normalize(name: str) -> str:
    """实体名归一化（去《》与空白）——孤儿与孪生按此键分组匹配。"""
    return _NORMALIZE_RE.sub("", name or "")


def _merge_plan(entities: list[dict]) -> dict:
    """分组内选出幸存者（连通优先、度数最高），其余作为待并入的孤儿。"""
    connected = [e for e in entities if e["deg"] > 0]
    pool = sorted(entities, key=lambda e: -e["deg"])
    survivor = pool[0]
    orphans = [e for e in entities if e["id"] != survivor["id"]]
    return {"survivor": survivor, "orphans": orphans, "has_connected": bool(connected)}


def cleanup(dry_run: bool) -> None:
    """主流程：查全量实体 → 按 (user_id, 归一化名) 分组 → 组内并合/删孤儿 → 无孪生幸存者恢复 MENTIONS。"""
    driver = GraphDatabase.driver(settings.NEO4J_URI,
                                  auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD))
    with driver.session() as s:
        rows = s.run(
            "MATCH (e:Entity) OPTIONAL MATCH (e)-[r]-() "
            "WITH e, count(r) AS deg RETURN e {.id, .name, .user_id, .display_name, "
            ".type_id, .description, .aliases, .confidence, .source_note_ids} AS e, deg"
        ).data()
        entities = [{"id": r["e"]["id"], "deg": r["deg"], **r["e"]} for r in rows]

        groups: dict[tuple, list[dict]] = {}
        for e in entities:
            groups.setdefault((e["user_id"], _normalize(e["name"])), []).append(e)

        zero_groups = {k: v for k, v in groups.items() if any(e["deg"] == 0 for e in v)}
        logger.info(f"实体总数 {len(entities)}，含零度节点的归一化分组 {len(zero_groups)} 个")

        merged = restored = 0
        for (uid, norm), members in sorted(zero_groups.items()):
            plan = _merge_plan(members)
            survivor, orphans = plan["survivor"], plan["orphans"]
            for orphan in orphans:
                logger.info(
                    f"[{'dry-run' if dry_run else 'merge'}] {orphan['name']!r}(deg={orphan['deg']}) "
                    f"并入 {survivor['name']!r}(deg={survivor['deg']}) user={uid}"
                )
                merged += 1
                if not dry_run:
                    s.run(
                        "MATCH (t:Entity {id: $tid}), (o:Entity {id: $oid}) "
                        "SET t.aliases = $aliases, t.confidence = $confidence, "
                        "    t.description = CASE WHEN coalesce(t.description,'') = '' THEN o.description ELSE t.description END, "
                        "    t.source_note_ids = $source_note_ids "
                        "DETACH DELETE o",
                        {
                            "tid": survivor["id"], "oid": orphan["id"],
                            "aliases": list(dict.fromkeys(
                                (survivor.get("aliases") or []) + (orphan.get("aliases") or []))),
                            "confidence": max(orphan.get("confidence") or 0.0,
                                              survivor.get("confidence") or 0.0),
                            "source_note_ids": list(dict.fromkeys(
                                (survivor.get("source_note_ids") or [])
                                + (orphan.get("source_note_ids") or []))),
                        },
                    ).consume()

            if not plan["has_connected"]:
                # 幸存者自身零度且无孪生：按 source_note_ids 恢复笔记 MENTIONS 边
                for nid in survivor.get("source_note_ids") or []:
                    logger.info(
                        f"[{'dry-run' if dry_run else 'restore'}] "
                        f"Note {nid} -[:MENTIONS]-> {survivor['name']!r}"
                    )
                    restored += 1
                    if not dry_run:
                        s.run(
                            "MATCH (n:Note {id: $nid, user_id: $uid}) "
                            "MATCH (e:Entity {id: $eid}) "
                            "WHERE NOT (n)-[:MENTIONS]->(e) "
                            "CREATE (n)-[:MENTIONS {id: randomUUID(), mention_count: 1, "
                            "context_json: $ctx}]->(e)",
                            {"nid": nid, "uid": uid, "eid": survivor["id"],
                             "ctx": json.dumps([], ensure_ascii=False)},
                        ).consume()

        if not dry_run:
            after = s.run(
                "MATCH (e:Entity) OPTIONAL MATCH (e)-[r]-() WITH e, count(r) AS deg "
                "RETURN sum(CASE WHEN deg = 0 THEN 1 ELSE 0 END) AS zero, count(e) AS total"
            ).single()
            logger.info(f"清理后：实体总数 {after['total']}，剩余零度 {after['zero']}")

        logger.info(f"完成：并入 {merged} 个，恢复 MENTIONS {restored} 条"
                    + ("（dry-run 未写库）" if dry_run else ""))
    driver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理 Neo4j 零度孤立实体")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)
