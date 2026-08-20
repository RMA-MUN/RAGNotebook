"""笔记模板 API 集成测试（真实 NoteTemplateService + SQLite）。"""


async def test_list_templates_seeds_defaults(client):
    resp = await client.get("/note-template/list", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    templates = resp.json()["data"]
    assert len(templates) == 6  # DEFAULT_TEMPLATES
    assert all(t["is_default"] for t in templates)


async def test_create_template(client):
    # 先列出默认模板（触发内置模板初始化），再创建自定义模板
    await client.get("/note-template/list", headers={"Authorization": "Bearer x"})
    resp = await client.post("/note-template/create", json={"name": "自定义模板"}, headers={"Authorization": "Bearer x"})
    body = resp.json()
    assert body["message"] == "模板创建成功"
    template = body["data"]
    assert template["name"] == "自定义模板"
    assert template["is_default"] is False
    assert template["sort_order"] == 6  # 默认 6 个之后


async def test_create_then_update_template(client):
    create_resp = await client.post("/note-template/create", json={"name": "旧名字"}, headers={"Authorization": "Bearer x"})
    tid = create_resp.json()["data"]["id"]
    resp = await client.put(f"/note-template/{tid}", json={"name": "新名字", "icon": "Star"},
                            headers={"Authorization": "Bearer x"})
    body = resp.json()
    assert body["message"] == "模板更新成功"
    assert body["data"]["name"] == "新名字"
    assert body["data"]["icon"] == "Star"


async def test_update_missing_template(client):
    resp = await client.put("/note-template/no-such-id", json={"name": "x"}, headers={"Authorization": "Bearer x"})
    assert resp.json()["message"] == "模板不存在或为内置模板"


async def test_delete_default_template_denied(client):
    resp = await client.get("/note-template/list", headers={"Authorization": "Bearer x"})
    default_id = resp.json()["data"][0]["id"]
    resp = await client.delete(f"/note-template/{default_id}", headers={"Authorization": "Bearer x"})
    assert resp.json()["message"] == "模板不存在或为内置模板"


async def test_delete_custom_template(client):
    create_resp = await client.post("/note-template/create", json={"name": "待删模板"}, headers={"Authorization": "Bearer x"})
    tid = create_resp.json()["data"]["id"]
    resp = await client.delete(f"/note-template/{tid}", headers={"Authorization": "Bearer x"})
    assert resp.json()["message"] == "模板删除成功"


async def test_reorder_templates(client):
    resp = await client.get("/note-template/list", headers={"Authorization": "Bearer x"})
    templates = resp.json()["data"]
    ids = [t["id"] for t in templates]
    reversed_ids = list(reversed(ids))

    resp = await client.put("/note-template/reorder", json={"ids": reversed_ids}, headers={"Authorization": "Bearer x"})
    assert resp.json()["message"] == "排序成功"

    # 校验排序生效
    resp = await client.get("/note-template/list", headers={"Authorization": "Bearer x"})
    assert [t["id"] for t in resp.json()["data"]] == reversed_ids


async def test_reorder_mismatch_fails(client):
    # 传一个不属于用户的假 ID
    resp = await client.put("/note-template/reorder", json={"ids": ["not-an-id"]}, headers={"Authorization": "Bearer x"})
    assert resp.json()["message"] == "排序失败，模板ID不匹配"