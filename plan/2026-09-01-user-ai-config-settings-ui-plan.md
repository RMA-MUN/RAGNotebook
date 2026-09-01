# 可视化配置 API Key / Base URL（按用户）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个登录用户在设置页可视化配置「对话/嵌入/视觉/云端重排序/联网搜索」五组模型的 API key / base url / model，配置按用户独立、用 `SECRET_KEY` 加密落库，调用时解析为该用户模型，未配置时回落应用级 `.env`。

**Architecture:** 新增 `user_ai_config` 表存每用户的五组配置（api_key 加密）；新增 `/config/ai` GET/PUT 端点；新增 `app/utils/user_config.py` 做「请求级用户模型解析」（用户填了 `base_url` 即视为已配置，api_key 为空自动回填占位符 `ollama`，完全未配则回落全局 `settings`）。启动预热全局模型保留作默认回退，`AgenticRagService.run`/`query_stream`/`DocumentProcessor` 等已持有 `user_id` 的调用点按用户取其模型。

**Tech Stack:** FastAPI · SQLAlchemy(async) · MySQL/SQLite · cryptography·Fernet · langchain-openai(ChatOpenAI/OpenAIEmbeddings) · React · react-i18next · sonner · axios

## Global Constraints

- api_key 一律用 `SECRET_KEY`（`app.core.settings.Settings.SECRET_KEY`）加密落库；`SECRET_KEY` 为空时 `PUT /config/ai` 返回 500，禁止明文落库。
- 判定「已配置」= 该能力 `base_url` 非空；api_key 为空时装 `USER_API_KEY_PLACEHOLDER = "ollama"`（Ollama `/v1` "required but ignored"），确保 `create_chat_openai`/`OpenAIEmbeddings` 永不见 `None`。
- 完全未配置的能力回落现有全局语义（`app.utils.factory._resolve_openai_config` 的原子回退），脚本/评测（无 user_id）不受影响。
- 模型新表必须登记到 `Base.metadata`（`backend/app/db/db_config.py` 与 `backend/tests/conftest.py` 的显式 import 列表）。
- 测试环境变量在 `tests/conftest.py` 顶部 import `app.*` 前设置；新增服务模块可被 `patch_session_factory` patch，故读库一律用 `app.db.db_config.AsyncSessionLocal`。
- 前端 `build` = `tsc -b && vite build`；`lint` = `eslint .`（在 `front/` 下执行）。后端测试命令：`python -m pytest tests/...`（Windows，backend 目录）。

---

### Task 1: UserAIConfig 数据模型 + DB 注册

**Files:**
- Create: `backend/app/models/user_ai_config.py`
- Modify: `backend/app/db/db_config.py:75`（模型 import 列表加 `user_ai_config`）
- Modify: `backend/tests/conftest.py:91`（模型 import 列表加 `user_ai_config`）
- Test: `backend/tests/test_models_orm.py`（新增一条 round-trip 测试）

**Interfaces:**
- Produces: `UserAIConfig`（SQLAlchemy model，PK=`user_id`，各能力列全可空，`updated_at`）。
- Consumes: `Base`（`from app.models.chat_history import Base`）、`Column/String/Boolean/DateTime/func`。

- [ ] **Step 1: 写失败测试**

把以下测试追加到 `backend/tests/test_models_orm.py` 末尾：

```python
def test_user_ai_config_roundtrip(db_session):
    from app.models.user_ai_config import UserAIConfig
    row = UserAIConfig(
        user_id="u-1",
        chat_base_url="http://localhost:11434/v1",
        chat_model="qwen3:8b",
        embed_base_url="http://localhost:11434/v1",
        web_search_enabled=True,
    )
    db_session.add(row)
    db_session.commit()
    loaded = db_session.get(UserAIConfig, "u-1")
    assert loaded is not None
    assert loaded.chat_base_url == "http://localhost:11434/v1"
    assert loaded.web_search_enabled is True
```

> 注：`db_session` fixture 来自 `tests/conftest.py`。若此文件当前无 `db_session` 用法，可新建 `backend/tests/test_models_orm.py` 并同样 import。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_models_orm.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'app.models.user_ai_config'`。

- [ ] **Step 3: 实现模型**

创建 `backend/app/models/user_ai_config.py`：

```python
from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.sql import func

from app.models.chat_history import Base


class UserAIConfig(Base):
    """每用户一组 AI 模型配置；各列可空，空=回落应用级 .env。api_key 列为加密文本"""

    __tablename__ = "user_ai_config"

    user_id = Column(String(36), primary_key=True)

    chat_base_url = Column(String(255), nullable=True)
    chat_api_key = Column(String(512), nullable=True)
    chat_model = Column(String(128), nullable=True)

    embed_base_url = Column(String(255), nullable=True)
    embed_api_key = Column(String(512), nullable=True)
    embed_model = Column(String(128), nullable=True)

    vision_base_url = Column(String(255), nullable=True)
    vision_api_key = Column(String(512), nullable=True)
    vision_model = Column(String(128), nullable=True)

    rerank_base_url = Column(String(255), nullable=True)
    rerank_api_key = Column(String(512), nullable=True)
    rerank_model = Column(String(128), nullable=True)

    web_search_enabled = Column(Boolean, default=False)
    web_search_api_key = Column(String(512), nullable=True)
    web_search_provider = Column(String(64), nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

- [ ] **Step 4: 注册到 Base.metadata**

`backend/app/db/db_config.py` 第 75 行改为（追加 `user_ai_config`）：

```python
    from app.models import chat_history, graph, note, note_template, review_record, user_model, user_ai_config  # noqa: F401
```

`backend/tests/conftest.py` 第 91 行同样追加：

```python
    from app.models import chat_history, graph, note, note_template, review_record, user_model, user_ai_config  # noqa: F401
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_models_orm.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/models/user_ai_config.py backend/app/db/db_config.py backend/tests/conftest.py backend/tests/test_models_orm.py
git commit -m "feat: 新增 UserAIConfig 模型并注册到 Base.metadata"
```

---

### Task 2: 密钥加密工具

**Files:**
- Create: `backend/app/utils/encryption.py`
- Test: `backend/tests/test_encryption.py`

**Interfaces:**
- Produces: `encrypt_secret(plain: str) -> str`、`decrypt_secret(token: str) -> str`。
- Consumes: `settings.SECRET_KEY`（`app.core.settings`）、`cryptography.fernet.Fernet`（`cryptography==48.0.0` 已存在）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_encryption.py`：

```python
import pytest

from app.utils.encryption import decrypt_secret, encrypt_secret


def test_encrypt_decrypt_roundtrip():
    token = encrypt_secret("sk-1234567890")
    assert token != "sk-1234567890"
    assert decrypt_secret(token) == "sk-1234567890"


def test_empty_is_identity():
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""


def test_missing_secret_key_raises(monkeypatch):
    from app.core.settings import settings
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    from app.utils.encryption import encrypt_secret
    with pytest.raises(RuntimeError):
        encrypt_secret("sk-x")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_encryption.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.utils.encryption'`。

- [ ] **Step 3: 实现**

创建 `backend/app/utils/encryption.py`：

```python
"""密钥加密：用 settings.SECRET_KEY 派生 Fernet 密钥做字段级对称加密。

约定：
- 空输入返回空串（不对空值加密，读取时空值直接落地为 NULL/空）。
- SECRET_KEY 为空时禁止加密（明确报错，避免明文落库）。
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.settings import settings


def _fernet() -> Fernet:
    if not settings.SECRET_KEY:
        raise RuntimeError("SECRET_KEY 未配置，无法加密 AI 配置中的密钥，请先在 .env 配置 SECRET_KEY")
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # 旧数据/明文残留：原样返回，交由调用方判断（已配置但解不开 → 视为退回 .env）
        return ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_encryption.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/utils/encryption.py backend/tests/test_encryption.py
git commit -m "feat: 新增 SECRET_KEY 派生的 Fernet 加解密工具"
```

---

### Task 3: 请求级用户模型解析（user_config.py）

**Files:**
- Create: `backend/app/utils/user_config.py`
- Test: `backend/tests/test_user_config.py`

**Interfaces:**
- Consumes:
  - `UserAIConfig`（Task 1）
  - `decrypt_secret`（Task 2）
  - `settings`（`app.core.settings`）
  - `AsyncSessionLocal`（`app.db.db_config`）
  - `create_chat_openai`、`_resolve_openai_config`、`resolve_vision_config`、`resolve_embed_config`（`app.utils.factory`）
- Produces:
  - `USER_API_KEY_PLACEHOLDER = "ollama"`
  - `async get_user_ai_config(user_id: str) -> UserAIConfig | None`
  - `def invalidate_user_config(user_id: str) -> None`
  - `async resolve_chat_config_for_user(user_id: str) -> dict`（keys: `model`, `api_key`, `base_url`）
  - `async resolve_embed_config_for_user(user_id: str) -> dict`
  - `async resolve_vision_config_for_user(user_id: str) -> dict`
  - `async create_chat_model_for_user(user_id: str, streaming: bool = True)`
  - `async create_embed_model_for_user(user_id: str)`
  - `async create_vision_model_for_user(user_id: str)`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_user_config.py`：

```python
import pytest

from app.models.user_ai_config import UserAIConfig
from app.utils.user_config import (
    USER_API_KEY_PLACEHOLDER,
    create_chat_model_for_user,
    get_user_ai_config,
    resolve_chat_config_for_user,
)


async def test_resolve_falls_back_to_env_when_unconfigured(monkeypatch, session_factory):
    from app.core.settings import settings
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-env", raising=False)
    monkeypatch.setattr(settings, "OPENAI_MODEL_NAME", "gpt-4o", raising=False)
    from app.utils.user_config import _CACHE
    _CACHE.clear()
    cfg = await resolve_chat_config_for_user("user-no-config")
    assert cfg["base_url"] == "https://api.openai.com/v1"
    assert cfg["api_key"] == "sk-env"
    assert cfg["model"] == "gpt-4o"


async def test_resolve_uses_user_config_and_placeholder(monkeypatch, session_factory):
    async with session_factory() as session:
        session.add(UserAIConfig(user_id="u-1", chat_base_url="http://localhost:11434/v1", chat_model="qwen3:8b"))
        await session.commit()
    from app.utils.user_config import _CACHE
    _CACHE.clear()
    cfg = await resolve_chat_config_for_user("u-1")
    assert cfg["base_url"] == "http://localhost:11434/v1"
    assert cfg["api_key"] == USER_API_KEY_PLACEHOLDER
    assert cfg["model"] == "qwen3:8b"
```

> 注：`session_factory` 来自 `tests/conftest.py`，其表包含 `user_ai_config`（Task 1 已注册）。测试里手动清 `_CACHE` 保证不命中缓存。若你希望更贴近生产，可加一个仅清当前用户的辅助；此处直接清空即可。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_user_config.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.utils.user_config'`。

- [ ] **Step 3: 实现**

创建 `backend/app/utils/user_config.py`：

```python
"""请求级用户模型配置解析：按用户取配置建模型，未配置回落全局 .env。

规则（全局约定，勿与 factory 原子回退冲突）：
- 该能力用户填了 base_url → 视为已配置：api_key=用户值或占位符，model=用户值或默认。
- 未配置 → 回落 app.utils.factory 的全局解析（原子回退）。
"""
import time

from sqlalchemy import select

from app.core.settings import settings
from app.db.db_config import AsyncSessionLocal
from app.models.user_ai_config import UserAIConfig
from app.utils.encryption import decrypt_secret
from app.utils.factory import create_chat_openai, resolve_embed_config, resolve_vision_config

USER_API_KEY_PLACEHOLDER = "ollama"

_CACHE_TTL_SECONDS = 30.0
_CACHE: dict[str, tuple[float, UserAIConfig | None]] = {}

_DEFAULTS = {"chat": "gpt-4o-mini", "embed": "text-embedding-v3", "vision": "qwen-vl-max"}


async def get_user_ai_config(user_id: str) -> UserAIConfig | None:
    now = time.time()
    hit = _CACHE.get(user_id)
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UserAIConfig).where(UserAIConfig.user_id == user_id))
        row = result.scalar_one_or_none()
    _CACHE[user_id] = (now, row)
    return row


def invalidate_user_config(user_id: str) -> None:
    _CACHE.pop(user_id, None)


async def resolve_chat_config_for_user(user_id: str) -> dict:
    row = await get_user_ai_config(user_id)
    if row is not None and row.chat_base_url:
        return {
            "model": row.chat_model or _DEFAULTS["chat"],
            "base_url": row.chat_base_url,
            "api_key": decrypt_secret(row.chat_api_key) or USER_API_KEY_PLACEHOLDER,
        }
    from app.utils.factory import resolve_chat_config

    return resolve_chat_config()


async def resolve_embed_config_for_user(user_id: str) -> dict:
    row = await get_user_ai_config(user_id)
    if row is not None and row.embed_base_url:
        return {
            "model": row.embed_model or _DEFAULTS["embed"],
            "base_url": row.embed_base_url,
            "api_key": decrypt_secret(row.embed_api_key) or USER_API_KEY_PLACEHOLDER,
        }
    return resolve_embed_config()


async def resolve_vision_config_for_user(user_id: str) -> dict:
    row = await get_user_ai_config(user_id)
    if row is not None and row.vision_base_url:
        return {
            "model": row.vision_model or _DEFAULTS["vision"],
            "base_url": row.vision_base_url,
            "api_key": decrypt_secret(row.vision_api_key) or USER_API_KEY_PLACEHOLDER,
        }
    return resolve_vision_config()


async def create_chat_model_for_user(user_id: str, streaming: bool = True):
    cfg = await resolve_chat_config_for_user(user_id)
    return create_chat_openai(
        model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
        streaming=streaming, top_p=0.7,
    )


async def create_embed_model_for_user(user_id: str):
    from langchain_openai import OpenAIEmbeddings

    cfg = await resolve_embed_config_for_user(user_id)
    if not (cfg["base_url"] and cfg["api_key"]):
        raise ValueError("嵌入模型配置不完整：请同时提供 base_url 与 api_key，或留空以回落 OPENAI_*")
    return OpenAIEmbeddings(
        model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
        check_embedding_ctx_length=False, chunk_size=10, timeout=30,
    )


async def create_vision_model_for_user(user_id: str):
    cfg = await resolve_vision_config_for_user(user_id)
    if not (cfg["base_url"] and cfg["api_key"]):
        return None
    return create_chat_openai(
        model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
        streaming=False, top_p=0.7,
    )
```

- [ ] **Step 4: 实现测试桩（让第二步真正的失败点成立）**

> 因 `get_user_ai_config` 依赖 `AsyncSessionLocal`，而 conftest 的 `patch_session_factory` 会把它换成 SQLite。在 `tests/conftest.py` 的 `patch_session_factory` 的 `targets` 里新增 `"app.utils.user_config": "AsyncSessionLocal"`，才能在本测试里命中 SQLite。

修改 `backend/tests/conftest.py` 第 136-142 行 `targets` 字典，追加一行：

```python
        "app.utils.user_config": "AsyncSessionLocal",
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_user_config.py -v`
Expected: PASS（两步均过）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/utils/user_config.py backend/tests/test_user_config.py backend/tests/conftest.py
git commit -m "feat: 请求级用户模型解析 user_config（含占位符与. env回退）"
```

---

### Task 4: 配置 API（/config/ai GET/PUT）+ 鉴权 + 打码 + 加密入库

**Files:**
- Create: `backend/app/router/config.py`
- Create: `backend/app/schemas/ai_config_schemas.py`
- Modify: `backend/app/main.py:52`（include `config_router`）
- Modify: `backend/tests/conftest.py:136-142`（`patch_session_factory` 加 `app.router.config`）
- Test: `backend/tests/test_config_api.py`

**Interfaces:**
- Consumes: `get_current_user_id`（`app.utils.auth_utils`）、`AsyncSessionLocal`、`UserAIConfig`（Task 1）、`encrypt_secret`/`decrypt_secret`（Task 2）、`invalidate_user_config`（Task 3）。
- Produces（前端阶段 2 依赖）:
  - `GET /config/ai -> { code, message, data: AIConfigOut }`
  - `PUT /config/ai` body `AIConfigIn -> { code, message, data: null }`
  - `AIConfigOut` shape（见 schema）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_config_api.py`：

```python
import pytest

from app.models.user_ai_config import UserAIConfig


async def test_get_returns_defaults_when_unconfigured(client, auth_headers):
    resp = await client.get("/config/ai", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["chat"]["api_key_set"] is False
    assert data["chat"]["base_url"] == ""
    assert data["embed"]["api_key_set"] is False


async def test_put_encrypts_and_get_masks(client, auth_headers, session_factory):
    payload = {
        "chat": {"base_url": "http://localhost:11434/v1", "api_key": "sk-real-secret", "model": "qwen3:8b"},
        "embed": {"base_url": "", "api_key": "", "model": ""},
        "vision": {"base_url": "", "api_key": "", "model": ""},
        "rerank": {"base_url": "", "api_key": "", "model": ""},
        "web_search": {"enabled": False, "api_key": "", "provider": ""},
    }
    resp = await client.put("/config/ai", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    # 落库的是密文
    async with session_factory() as session:
        row = await session.get(UserAIConfig, "test-user")
        assert row is not None
        assert row.chat_api_key != "sk-real-secret"
        assert "sk-real-secret" in row.chat_api_key is False
    # GET 不回明文
    resp2 = await client.get("/config/ai", headers=auth_headers)
    data = resp2.json()["data"]
    assert data["chat"]["api_key_set"] is True
    assert data["chat"]["api_key_masked"]
    assert "sk-real-secret" not in data["chat"]["api_key_masked"]


async def test_put_requiring_encryption_secret(client, auth_headers, monkeypatch):
    from app.core.settings import settings
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    payload = {"chat": {"base_url": "http://x", "api_key": "k", "model": "m"},
               "embed": {"base_url": "", "api_key": "", "model": ""},
               "vision": {"base_url": "", "api_key": "", "model": ""},
               "rerank": {"base_url": "", "api_key": "", "model": ""},
               "web_search": {"enabled": False, "api_key": "", "provider": ""}}
    resp = await client.put("/config/ai", json=payload, headers=auth_headers)
    assert resp.status_code == 500
```

> 注：client fixture 把 `get_current_user_id` override 为 `TEST_USER_ID`（`tests/fakes.TEST_USER_ID = "test-user"`）。`session_factory` 拿到 SQLite 连接，可直接查密文。`patch_session_factory` 需认到 `app.router.config`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_config_api.py -v`
Expected: FAIL（404 `/config/ai` 未注册）。

- [ ] **Step 3: 实现 schemas**

创建 `backend/app/schemas/ai_config_schemas.py`：

```python
from datetime import datetime

from pydantic import BaseModel


class CapabilityIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class WebSearchIn(BaseModel):
    enabled: bool = False
    api_key: str = ""
    provider: str = ""


class AIConfigIn(BaseModel):
    chat: CapabilityIn = CapabilityIn()
    embed: CapabilityIn = CapabilityIn()
    vision: CapabilityIn = CapabilityIn()
    rerank: CapabilityIn = CapabilityIn()
    web_search: WebSearchIn = WebSearchIn()


class CapabilityOut(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key_set: bool = False
    api_key_masked: str = ""


class WebSearchOut(BaseModel):
    enabled: bool = False
    provider: str = ""
    api_key_set: bool = False
    api_key_masked: str = ""


class AIConfigOut(BaseModel):
    chat: CapabilityOut
    embed: CapabilityOut
    vision: CapabilityOut
    rerank: CapabilityOut
    web_search: WebSearchOut
    updated_at: datetime | None = None
```

- [ ] **Step 4: 实现 router**

创建 `backend/app/router/config.py`：

```python
"""AI 配置端点：按用户读写 /config/ai。

约定：
- GET 只回打码（api_key_masked），绝不回明文；用 api_key_set 标记是否已配置。
- PUT upsert；api_key 为空=清空；非空则 encrypt_secret 后落库；成功后 invalidate_user_config。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.core.logger_handler import logger
from app.core.success_response import success_response
from app.db.db_config import AsyncSessionLocal
from app.models.user_ai_config import UserAIConfig
from app.schemas.ai_config_schemas import AIConfigIn, AIConfigOut, CapabilityOut, WebSearchOut
from app.utils.auth_utils import get_current_user_id
from app.utils.encryption import decrypt_secret, encrypt_secret
from app.utils.user_config import invalidate_user_config

config_router = APIRouter(tags=["config"], prefix="/config")


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return "****" + key[-4:]


def _capability_out(base_url, model, api_key_cipher) -> CapabilityOut:
    return CapabilityOut(
        base_url=base_url or "",
        model=model or "",
        api_key_set=bool(api_key_cipher),
        api_key_masked=_mask(decrypt_secret(api_key_cipher)),
    )


@config_router.get("/ai")
async def get_ai_config(user_id: str = Depends(get_current_user_id)):
    async with AsyncSessionLocal() as session:
        row = await session.get(UserAIConfig, user_id)
    if row is None:
        empty_cap = CapabilityOut()
        return success_response(data=AIConfigOut(
            chat=empty_cap, embed=empty_cap, vision=empty_cap, rerank=empty_cap,
            web_search=WebSearchOut(), updated_at=None,
        ).model_dump())
    return success_response(data=AIConfigOut(
        chat=_capability_out(row.chat_base_url, row.chat_model, row.chat_api_key),
        embed=_capability_out(row.embed_base_url, row.embed_model, row.embed_api_key),
        vision=_capability_out(row.vision_base_url, row.vision_model, row.vision_api_key),
        rerank=_capability_out(row.rerank_base_url, row.rerank_model, row.rerank_api_key),
        web_search=WebSearchOut(
            enabled=bool(row.web_search_enabled),
            provider=row.web_search_provider or "",
            api_key_set=bool(row.web_search_api_key),
            api_key_masked=_mask(decrypt_secret(row.web_search_api_key)),
        ),
        updated_at=row.updated_at,
    ).model_dump())


@config_router.put("/ai")
async def put_ai_config(req: AIConfigIn, user_id: str = Depends(get_current_user_id)):
    async with AsyncSessionLocal() as session:
        row = await session.get(UserAIConfig, user_id)
        if row is None:
            row = UserAIConfig(user_id=user_id)
            session.add(row)
        row.chat_base_url = req.chat.base_url or None
        row.chat_api_key = encrypt_secret(req.chat.api_key) or None
        row.chat_model = req.chat.model or None
        row.embed_base_url = req.embed.base_url or None
        row.embed_api_key = encrypt_secret(req.embed.api_key) or None
        row.embed_model = req.embed.model or None
        row.vision_base_url = req.vision.base_url or None
        row.vision_api_key = encrypt_secret(req.vision.api_key) or None
        row.vision_model = req.vision.model or None
        row.rerank_base_url = req.rerank.base_url or None
        row.rerank_api_key = encrypt_secret(req.rerank.api_key) or None
        row.rerank_model = req.rerank.model or None
        row.web_search_enabled = req.web_search.enabled
        row.web_search_api_key = encrypt_secret(req.web_search.api_key) or None
        row.web_search_provider = req.web_search.provider or None
        await session.commit()
        await session.refresh(row)
    invalidate_user_config(user_id)
    logger.info(f"用户 {user_id} 已更新 AI 配置")
    return success_response(message="AI 配置已保存")
```

> `encrypt_secret` / `decrypt_secret` 在 `SECRET_KEY` 为空时会 `raise RuntimeError`；由全局异常处理（`register_exception_handlers`）转 500。

- [ ] **Step 5: 注册路由**

`backend/app/main.py` 第 21 行附近 import：

```python
from app.router.config import config_router
```

第 52 行后追加：

```python
app.include_router(config_router)
```

- [ ] **Step 6: 更新测试 patch 目标**

`backend/tests/conftest.py` 第 136-142 行 `targets` 追加：

```python
        "app.router.config": "AsyncSessionLocal",
```

- [ ] **Step 7: 跑测试确认通过**

Run: `python -m pytest tests/test_config_api.py -v`
Expected: PASS（三条）。

- [ ] **Step 8: 提交**

```bash
git add backend/app/router/config.py backend/app/schemas/ai_config_schemas.py backend/app/main.py backend/tests/conftest.py backend/tests/test_config_api.py
git commit -m "feat: 新增 /config/ai 读写端点（打码+加密落库+缓存失效）"
```

---

### Task 5: 对话/LLM 每用户接线（AgenticRagService + agent.py + local_retriever）

**Files:**
- Modify: `backend/app/rag/agentic_rag/service.py`（`run()` 构造 per-user 组件）
- Modify: `backend/app/rag/agentic_rag/local_retriever.py`（`__init__` 接收 `embed_model`；`_query_embedding` 用它）
- Modify: `backend/app/agent/agent.py`（`get_agent_stream_response` 接收 `chat_model`，`_create_chat_model` 支持覆盖）
- Modify: `backend/app/router/chat.py:52,82`（`query_stream` 解析 per-user chat/embed 并传入）
- Test: `backend/tests/rag/test_agentic_rag_service.py`（补充）；`backend/tests/test_chat_api.py`（冒烟）

**Interfaces:**
- Consumes: `create_chat_model_for_user` / `create_embed_model_for_user` / `get_user_ai_config`（Task 3）。
- Produces: `AgenticRagService.run(query, user_id, thinking_callback)` 行为兼容但 per-user；`LocalRetriever(embed_model=...)`；`get_agent_stream_response(..., chat_model=None)`。

- [ ] **Step 1: 让 local_retriever 支持 embed_model 注入**

`backend/app/rag/agentic_rag/local_retriever.py` 的 `__init__`（第 24-32 行）增加 `embed_model` 参数并保存：

```python
    def __init__(
        self,
        note_service: Any | None = None,
        session_factory: Callable[[], Any] = AsyncSessionLocal,
        query_entity_extractor: QueryEntityExtractor | None = None,
        embed_model: Any | None = None,
    ):
        self.note_service = note_service
        self.session_factory = session_factory
        self.query_entity_extractor = query_entity_extractor
        self.embed_model = embed_model
```

`_query_embedding`（第 121-132 行）改为实例方法并优先用注入的 embed_model：

```python
    async def _query_embedding(self, query: str) -> list[float] | None:
        model = self.embed_model
        if model is None:
            from app.core.background_init import init_manager

            model = init_manager.embed_model
        if model is None or not query:
            return None
        try:
            return await asyncio.to_thread(model.embed_query, query)
        except Exception:
            return None
```

> 调用点 `self._query_embedding(step.query)` 已存在（第 58 行），无需改调用处。

`_entity_candidates`（第 110-119 行）目前每次 `QueryEntityExtractor()`；改为使用注入的 extractor 若无注入则回退默认：

```python
    async def _entity_candidates(self, query: str) -> list[str]:
        extractor = self.query_entity_extractor or QueryEntityExtractor()
        try:
            return await extractor.extract(query)
        except Exception as e:
            from app.core.logger_handler import logger

            logger.warning(f"查询实体抽取失败，回落规则: {query}: {e}")
            return QueryEntityExtractor._fallback_candidates(query)
```

（现行代码已基本如此，保留即可。）

- [ ] **Step 2: AgenticRagService.run 按用户建组件**

`backend/app/rag/agentic_rag/service.py` 的 `run()`（第 31 行起）开头（`plan = await self.planner.plan(query)` 前）构造 per-user 组件并用于取 retrieval plan 与后续检索：

```python
        user_chat = await create_chat_model_for_user(user_id)
        user_embed = await create_embed_model_for_user(user_id)
        planner = AgenticRagPlanner(chat_model=user_chat)
        evaluator = AnswerabilityEvaluator(chat_model=user_chat)
        retriever = self.local_retriever if user_embed is None else LocalRetriever(
            note_service=self.local_retriever.note_service,
            session_factory=self.local_retriever.session_factory,
            query_entity_extractor=QueryEntityExtractor(chat_model=user_chat),
            embed_model=user_embed,
        )
        plan = await planner.plan(query)
```

把 `run()` 中后续引用的 `self.local_retriever` 与 `self.evaluator` 替换为上方 `retriever` / `evaluator`（`self.local_retriever.search(...)` → `retriever.search(...)`、`self.evaluator.evaluate(...)` → `evaluator.evaluate(...)`）。

在文件顶部补 import：

```python
from app.rag.agentic_rag.query_entity_extractor import QueryEntityExtractor
from app.utils.user_config import create_chat_model_for_user, create_embed_model_for_user
```

> `create_embed_model_for_user` 在用户未配置且全局未配嵌入时可能 raise ValueError，这里应 try/except 退回 `None`（即仅用户显式配置时启用 per-user 嵌入；否则沿用全局）——在 `create_embed_model_for_user` 调用外加 try 并在异常时 `user_embed = None`。

- [ ] **Step 3: agent 流式链路接收 per-user chat 模型**

`backend/app/agent/agent.py`：

- `_create_chat_model(self, custom_model=None)`（第 89 行）增加 `api_key`/`base_url` 覆盖参数：

```python
    def _create_chat_model(self, custom_model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        from app.utils.factory import create_chat_openai

        model = custom_model or settings.OPENAI_MODEL_NAME or "gpt-4o-mini"
        logger.info(f"🤖 Agent使用OpenAI兼容模型: {model}")
        return create_chat_openai(
            model=model,
            api_key=api_key or (settings.OPENAI_API_KEY or None),
            base_url=base_url or (settings.OPENAI_BASE_URL or None),
            streaming=True,
            top_p=0.7,
        )
```

- `get_agent_stream_response`（第 225 行）加 `chat_model: object | None = None` 参数，`run_agent` 内用它创建 agent：

```python
async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: list[BaseTool] | None = None,
        rag_context: str = "",
        chat_model: object | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
```

`run_agent` 内创建 agent 处：若 `chat_model is not None`，则不经 `agent_factory.create_agent()` 的默认建模型，而用 `agent_factory.create_agent(custom_tools, custom_model=chat_model)` —— 需让 `create_agent(custom_model=...)` 接受已构建实例：把 `AgentFactory._create_chat_model` 改成若 `custom_model` 已是模型实例则直接返回。为此给 `create_agent` 增加：

```python
    def create_agent(self, custom_tools=None, custom_model=None, custom_system_prompt=None, **kwargs):
        chat_model = custom_model if isinstance(custom_model, object) and getattr(custom_model, "ainvoke", None) else self._create_chat_model(custom_model)
```

即：传入模型实例时直接复用，否则按名称构建。随后 `get_agent_stream_response` 内把 `create_agent(...)` 调用改为 `agent_factory.create_agent(custom_tools=custom_tools, custom_model=chat_model, ...)`。

> 实现时保持 `run_agent` 现有 `set_current_user_id`/`set_thinking_callback`/history/prompt 逻辑不变，仅替换模型来源。

- [ ] **Step 4: query_stream 传递 per-user 模型**

`backend/app/router/chat.py`：

- `stream_with_rag_thinking` 内，进入 `run_rag` 前解析一次：

```python
        user_chat = await create_chat_model_for_user(user_id)
```

- `AgenticRagService().run(...)` 内部已按 Task 5 Step 2 自行解析，此处无需传。
- 第 82 行 `get_agent_stream_response(request.query, session_id, user_id, rag_context=rag_context, chat_model=user_chat)` 传入 per-user chat 模型。

顶部补 import：

```python
from app.utils.user_config import create_chat_model_for_user
```

- [ ] **Step 5: 跑测试确认既有链路不回归**

Run: `python -m pytest tests/rag/test_agentic_rag_service.py tests/test_chat_api.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/rag/agentic_rag/service.py backend/app/rag/agentic_rag/local_retriever.py backend/app/agent/agent.py backend/app/router/chat.py
git commit -m "feat: 对话/检索/Agent 链路按用户解析模型"
```

---

### Task 6: 上传嵌入与视觉 per-user

**Files:**
- Modify: `backend/app/rag/vector_store.py`（`get_document` 传 per-user embed）
- Modify: `backend/app/rag/document_handler/processor.py`（`get_document` 接收 embed_model 覆盖）
- Modify: `backend/app/services/note_service.py`（视觉：`vision_service` 调用处传 per-user vision）— 仅在确认存在 vision 调用时处理，否则跳过

**Interfaces:**
- Consumes: `create_embed_model_for_user` / `create_vision_model_for_user` / `get_user_ai_config`（Task 3）。
- Produces: `VectorStoreService.get_document(files, user_id, progress_callback)` 内部用 per-user embed。

- [ ] **Step 1: 上传嵌入 per-user**

`backend/app/rag/document_handler/processor.py` 的 `get_document`（第 ~30+ 行）增加参数并在内部覆盖实例嵌入：

```python
    async def get_document(self, files: list = None, user_id: str = None, progress_callback=None, embed_model=None):
        effective_embed = embed_model or self.embed_model
```

选取体内使用 `self.embed_model` 的位置改为 `effective_embed`（保持初始化时 `embedding_model=embed_model` 逻辑不变）。

`backend/app/rag/vector_store.py` 的 `get_document`（第 354-356 行）解析 per-user embed 并透传：

```python
    async def get_document(self, files: list = None, user_id: str = None, progress_callback=None):
        from app.utils.user_config import create_embed_model_for_user, get_user_ai_config

        embed_model = None
        row = await get_user_ai_config(user_id) if user_id else None
        if row is not None and row.embed_base_url:
            embed_model = await create_embed_model_for_user(user_id)
        await self.document_processor.get_document(files, user_id, progress_callback, embed_model=embed_model)
```

> `create_embed_model_for_user` 可能 raise——用 try/except 包住，异常时 `embed_model = None`（沿用全局），不阻塞上传。

- [ ] **Step 2: 视觉 per-user**

`backend/app/utils/vision_service.py` 使用 `init_manager.vision_model`；改为若用户配置了 vision_base_url 则用 per-user 模型。在调用方（`note_service`/`processor` 使用 vision_service 处）解析出 per-user vision 并透传，或在 `vision_service` 增加一个用户可配置入口。优先最小改动：在 `vision_service` 公开函数增加可选 `model` 参数，调用处按用户传入（用 `create_vision_model_for_user` 兜底）。

> 若 `vision_service.py` 当前无清晰可注入点，本步骤以「在 `backend/app/rag/document_handler/processor.py` 处理 PDF 时按用户传入 vision 模型」落地；否则标记为本步骤在验收时确认视觉调用点。

- [ ] **Step 3: 跑测试确认不回归**

Run: `python -m pytest tests/rag/ tests/test_document_processor.py tests/test_vision_service.py -v`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add backend/app/rag/vector_store.py backend/app/rag/document_handler/processor.py backend/app/utils/vision_service.py
git commit -m "feat: 上传嵌入与视觉模型按用户解析"
```

---

### Task 7: 云端重排序 + 联网搜索 per-user + docstring 清理

**Files:**
- Modify: `backend/app/rag/reorder_service.py`（`ReorderService`/`reorder_documents` 支持 per-user 配置）
- Modify: `backend/app/rag/agentic_rag/web_search.py`（`WebSearchClient` 支持每用户 enabled/provider/key）
- Modify: `backend/app/rag/agentic_rag/service.py`（`run` 用 per-user `WebSearchClient`）
- Modify: `backend/app/router/chat.py:138`、`backend/app/router/chat_service.py:39`（清理过时本地重排序 docstring）

**Interfaces:**
- Consumes: `get_user_ai_config`（Task 3）、`decrypt_secret`（Task 2）。

- [ ] **Step 1: 重排序 per-user**

`backend/app/rag/reorder_service.py`：增加模块函数 `async def get_reorder_config_for_user(user_id) -> dict`，返回 `{base_url, api_key, model, enabled}`（读用户 `rerank_*`，未配回退 `.env` `RERANKER_*`）；`reorder_documents` 增加可选 `config: dict | None` 参数，非空时用其覆盖 `self.api_base_url/api_key/model`。`chat_service.handle_reorder`/`chat.py reorder` 已有 user 上下文时传入。

> 说明：`/chat/reorder` 端点（`chat.py:132`）当前无 `user_id`——若需要 per-user 需加 `Depends(get_current_user_id)` 并透传。按需保留全局默认，未传则沿用模块级配置。

- [ ] **Step 2: 联网搜索 per-user**

`backend/app/rag/agentic_rag/service.py` 的 `run()`：`self.web_search_client` 若用户配置了 `web_search_enabled` 且 provider/key 有值，改为构造 `WebSearchClient(enabled=..., provider=..., api_key=...)`（`decrypt_secret` 解 key），否则沿用默认 `self.web_search_client`。

`backend/app/rag/agentic_rag/web_search.py` 现有 `WebSearchClient.__init__(enabled, provider, api_key, http_client_factory)` 已支持注入，无需大改；仅需在 `service.py` 解析后传入。

- [ ] **Step 3: 清理过时 docstring**

- `backend/app/router/chat.py:138` 改为：`"""使用云端 rerank 对文档进行重排序"""`。
- `backend/app/router/chat_service.py:39` 改为：`"""使用云端 rerank 对文档进行重排序（RERANKER_* 配置）"""`。

- [ ] **Step 4: 跑测试确认不回归**

Run: `python -m pytest tests/rag/test_agentic_rag_web_search.py tests/rag/test_reorder_service.py tests/test_chat_api.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/rag/reorder_service.py backend/app/rag/agentic_rag/web_search.py backend/app/rag/agentic_rag/service.py backend/app/router/chat.py backend/app/router/chat_service.py
git commit -m "feat: 云端重排序与联网搜索按用户解析，清理过时注释"
```

---

### Task 8: 前端 Settings UI + api + i18n

**Files:**
- Create: `front/src/api/aiConfig.ts`
- Modify: `front/src/api/endpoints.ts`（加 `aiConfig: '/config/ai'`）
- Modify: `front/src/types/api.ts`（加 AIConfig 相关类型）
- Modify: `front/src/pages/Settings.tsx`
- Modify: `front/src/i18n/locales/zh-CN.ts`、`front/src/i18n/locales/en-US.ts`（加 settings.ai.* 键）

**Interfaces:**
- Consumes: `GET/PUT /config/ai`（Task 4 的 `AIConfigOut` shape）。
- Produces: `aiConfigApi.getAiConfig()` / `aiConfigApi.saveAiConfig(payload)`。

- [ ] **Step 1: api 客户端**

创建 `front/src/api/aiConfig.ts`：

```typescript
import client from './client'
import { endpoints } from './endpoints'
import type { AIConfig, AIConfigPayload } from '../types/api'

export const aiConfigApi = {
  get: async (): Promise<AIConfig> => {
    const res = await client.get<{ data: AIConfig }>(endpoints.aiConfig)
    return res.data.data
  },
  save: async (payload: AIConfigPayload): Promise<void> => {
    await client.put<{ data: null }>(endpoints.aiConfig, payload)
  },
}
```

`front/src/api/endpoints.ts` 追加：

```typescript
  // AI config
  aiConfig: '/config/ai',
```

- [ ] **Step 2: 类型**

`front/src/types/api.ts` 追加：

```typescript
export interface CapabilityConfig {
  base_url: string
  model: string
  api_key_set: boolean
  api_key_masked: string
}

export interface WebSearchConfig {
  enabled: boolean
  provider: string
  api_key_set: boolean
  api_key_masked: string
}

export interface AIConfig {
  chat: CapabilityConfig
  embed: CapabilityConfig
  vision: CapabilityConfig
  rerank: CapabilityConfig
  web_search: WebSearchConfig
  updated_at: string | null
}

export interface CapabilityPayload {
  base_url: string
  api_key: string
  model: string
}

export interface AIConfigPayload {
  chat: CapabilityPayload
  embed: CapabilityPayload
  vision: CapabilityPayload
  rerank: CapabilityPayload
  web_search: { enabled: boolean; api_key: string; provider: string }
}
```

- [ ] **Step 3: Settings UI**

在 `front/src/pages/Settings.tsx` 顶部 import：

```typescript
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'
import { aiConfigApi } from '../api/aiConfig'
import type { AIConfig, CapabilityPayload } from '../types/api'
```

新增一个可复用的 `CapabilityCard`（页面内组件）与主状态：

```tsx
const EMPTY_CAP: CapabilityPayload = { base_url: '', api_key: '', model: '' }

export default function Settings() {
  const { t } = useTranslation()
  const { theme, setTheme } = useThemeStore()
  const { lang, setLang } = useLanguageStore()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [chat, setChat] = useState<CapabilityPayload>(EMPTY_CAP)
  const [embed, setEmbed] = useState<CapabilityPayload>(EMPTY_CAP)
  const [vision, setVision] = useState<CapabilityPayload>(EMPTY_CAP)
  const [rerank, setRerank] = useState<CapabilityPayload>(EMPTY_CAP)
  const [web, setWeb] = useState<{ enabled: boolean; api_key: string; provider: string }>({ enabled: false, api_key: '', provider: '' })

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await aiConfigApi.get()
        setChat({ base_url: cfg.chat.base_url, api_key: '', model: cfg.chat.model })
        setEmbed({ base_url: cfg.embed.base_url, api_key: '', model: cfg.embed.model })
        setVision({ base_url: cfg.vision.base_url, api_key: '', model: cfg.vision.model })
        setRerank({ base_url: cfg.rerank.base_url, api_key: '', model: cfg.rerank.model })
        setWeb({ enabled: cfg.web_search.enabled, api_key: '', provider: cfg.web_search.provider })
      } catch {
        toast.error(t('settings.ai.loadFailed'))
      } finally {
        setLoading(false)
      }
    })()
  }, [t])

  const handleSave = async () => {
    setSaving(true)
    try {
      await aiConfigApi.save({ chat, embed, vision, rerank, web_search: web })
      toast.success(t('settings.ai.saved'))
    } catch (e) {
      toast.error(t('settings.ai.saveFailed'))
    } finally {
      setSaving(false)
    }
  }
```

新增渲染块（放在语言卡片后、`</div>` 前）——一个封装 `ai` 区；每个能力卡含 base_url / api_key(password+显隐) / model。为控制篇幅，`CapabilityCard` 用受控 props 实现，具体可视化结构见下述说明；**必须在页面上渲染出五个子卡与保存按钮**。

> 由于本任务代码量大，实际实现时在 `Settings.tsx` 内新增一个 `CapabilityCard` 组件（props：`title`、`value: CapabilityPayload`、`onChange`、`placeholder`、`t`），并循环渲染 `chat/embed/vision/rerank` 四卡 + `web_search` 专属卡（含 enabled 开关与 provider）。api_key 用 `type={showKey ? 'text' : 'password'}` + 显隐按钮；placeholder 显示 `t('settings.ai.keyHint')`。

- [ ] **Step 4: i18n 键**

`front/src/i18n/locales/zh-CN.ts` 与 `en-US.ts` 的 `settings` 对象下追加：

```ts
      ai: {
        title: 'AI 模型配置',  // en: 'AI Model Config'
        chat: '对话/LLM',      // en: 'Chat / LLM'
        embed: '嵌入模型',     // en: 'Embedding'
        vision: '视觉模型',    // en: 'Vision'
        rerank: '重排序(云端)', // en: 'Rerank (Cloud)'
        web: '联网搜索',       // en: 'Web Search'
        baseUrl: 'Base URL',
        apiKey: 'API Key',
        model: 'Model',
        keyHint: '云端必填，本地 Ollama 可留空/填占位符', // en: 'Required for cloud; local Ollama may leave blank/placeholder'
        enabled: '启用',
        provider: 'Provider',
        save: '保存',
        saving: '保存中…',
        saved: '已保存',
        loadFailed: '加载 AI 配置失败',
        saveFailed: '保存失败',
        reindexHint: '更换嵌入模型后需重新上传/重建索引', // en: 'Re-upload/re-index after changing embedding model'
      },
```

> 具体 key 命名以现有 `zh-CN.ts`/`en-US.ts` 的 `settings` 块为准，保持 JSON/TS 对象结构与既有键一致；上面为占位映射，实施时对应替换。

- [ ] **Step 5: 验证前端**

Run（`front/` 目录）：`npm run build`
Expected: 通过（`tsc -b` 无类型错误 + vite build 成功）。

- [ ] **Step 6: 提交**

```bash
git add front/src/api/aiConfig.ts front/src/api/endpoints.ts front/src/types/api.ts front/src/pages/Settings.tsx front/src/i18n/locales/zh-CN.ts front/src/i18n/locales/en-US.ts
git commit -m "feat: Settings 页新增 AI 模型配置可视化（GET/PUT）"
```

---

### Task 9: 端到端验证与收尾

**Files:**
- 无新增；执行验证。

- [ ] **Step 1: 后端全量测试**

Run（`backend/` 目录）：`python -m pytest -q`
Expected: 全绿（新增 test_config_api / test_user_config / test_encryption 通过，既有不回归）。

- [ ] **Step 2: 后端 lint/类型（如项目配置）**

若有 ruff/mypy：`ruff check app tests` / `mypy app`（无则跳过），确保无新告警。

- [ ] **Step 3: 前端 lint**

Run（`front/` 目录）：`npm run lint`
Expected: 无 error。

- [ ] **Step 4: 手动冒烟（可选，需运行后端+前端 dev）**

- 登录 → 设置页 → 填 Chat base_url=`http://localhost:11434/v1` + model=`qwen3:8b`，key 留空 → 保存 → 提示已保存 → 刷新回填 api_key 为隐式占位。
- 到 `/chat` 发起问题，确认 Agent/检索正常（即 per-user 模型生效，无 missing-key 报错）。

- [ ] **Step 5: 最终提交**

```bash
git add -A
git commit -m "test: 端到端验证通过"
```

---

## Self-Review 记录

- **Spec coverage**：数据模型①④；加密②；解析③；端点④；对话/LLM 接线⑤；嵌入/视觉⑥；重排序/搜索⑦；前端⑧；测试⑨。
- **Type consistency**：`AIConfigOut`（Task 4）与 `front/types/api.ts`（Task 8）字段一致（`chat/embed/vision/rerank.web_search`，`api_key_set`/`api_key_masked`）；`resolve_*_config_for_user`/`create_*_model_for_user` 名称在 Task 3 定义、Task 5-7 使用一致。
- **竞态/边界**：`get_user_ai_config` 缓存 30s，PUT 后 `invalidate_user_config` 清缓存；`create_embed_model_for_user` 异常时调用方 try/except 回退全局。
