import asyncio
import uuid
from app.db.db_config import AsyncSessionLocal
from sqlalchemy import text

async def seed():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("SELECT DISTINCT user_id FROM notes LIMIT 1"))
        row = r.fetchone()
        if not row:
            print("No user found")
            return
        user_id = row[0]
        print(f"Seeding for user: {user_id}")

        # Check existing count
        r = await db.execute(text("SELECT COUNT(*) FROM note_templates WHERE user_id = :uid"), {"uid": user_id})
        existing = r.scalar()
        print(f"Existing templates: {existing}")
        if existing > 0:
            print("Templates already exist, skipping")
            return

        templates = [
            ("空白笔记", "FileText", "", "", "", "[]"),
            ("会议纪要", "Users", "work", "会议纪要 - ", "## 会议信息\n- **时间**：\n- **参与人**：\n- **主题**：\n\n## 会议内容\n\n\n## 待办事项\n- [ ] \n", '["会议"]'),
            ("学习笔记", "GraduationCap", "study", "", "## 学习目标\n\n\n## 核心内容\n\n\n## 总结与反思\n\n", '["学习"]'),
            ("日记", "BookOpen", "life", "", "## 今日记录\n\n\n## 心情\n\n\n## 明日计划\n- [ ] \n", '["日记"]'),
            ("项目计划", "ListTodo", "project", "", "## 项目概述\n\n\n## 目标\n- [ ] \n\n## 里程碑\n| 阶段 | 内容 | 截止日期 | 状态 |\n|------|------|----------|------|\n| 1    |      |          | 待开始 |\n\n## 备注\n\n", '["项目"]'),
            ("读书笔记", "BookMarked", "study", "", "## 书籍信息\n- **书名**：\n- **作者**：\n\n## 核心观点\n\n\n## 精彩摘录\n\n\n## 读后感\n\n", '["读书"]'),
        ]

        for name, icon, cat, title, content, tags in templates:
            tid = str(uuid.uuid4())
            await db.execute(
                text("INSERT INTO note_templates (id, user_id, name, icon, category, title, content, tags, is_default) VALUES (:id, :uid, :name, :icon, :cat, :title, :content, CAST(:tags AS JSON), 1)"),
                {"id": tid, "uid": user_id, "name": name, "icon": icon, "cat": cat, "title": title, "content": content, "tags": tags},
            )

        await db.commit()
        r = await db.execute(text("SELECT COUNT(*) FROM note_templates"))
        print(f"Total templates after seed: {r.scalar()}")

asyncio.run(seed())
