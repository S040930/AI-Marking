# 计划：修复 3 个 Bug + 更新 README + 补最小测试

## 概述

修复审计发现的 3 个 Bug（`details` 类型标注错误、`file_path` 泄露、时区不一致），更新过时的 README，并补充后端最小测试覆盖。

---

## 当前状态分析

### Bug 1（严重）：`details` 类型标注错误，成功批改后接口 500

LLM 返回的 `details` 是**数组** `[{criterion, score, comment}]`（见 [prompt.py:23](file:///Users/mac/Desktop/AI-Marking/backend/app/core/prompt.py#L23) 和 [llm.py:101](file:///Users/mac/Desktop/AI-Marking/backend/app/services/llm.py#L101)），但模型和 schema 标注成了 `dict`：

- [models/submission.py:46](file:///Users/mac/Desktop/AI-Marking/backend/app/models/submission.py#L46)：`details: Mapped[dict | None]`
- [schemas/submission.py:31](file:///Users/mac/Desktop/AI-Marking/backend/app/schemas/submission.py#L31)：`details: dict | None = None`

Pydantic v2 在序列化 `SubmissionDetail` 时会将 list 值校验为 `dict | None` → 抛 `ValidationError` → `GET /api/submissions/{id}` 返回 500。

前端 [submissions.ts:31](file:///Users/mac/Desktop/AI-Marking/frontend/src/api/submissions.ts#L31) 已正确标注为 `DetailItem[] | null`，无需改动。

**无需 Alembic 迁移**：SQLAlchemy `JSON` 列可存任意 JSON 值，改类型标注不影响 DB schema。

### Bug 2（安全）：`file_path` 泄露到 API 响应

- [schemas/submission.py:33](file:///Users/mac/Desktop/AI-Marking/backend/app/schemas/submission.py#L33)：`SubmissionDetail` 暴露 `file_path: str | None = None`
- 前端 [submissions.ts:33](file:///Users/mac/Desktop/AI-Marking/frontend/src/api/submissions.ts#L33)：`file_path: string;`（但 `ResultPage.tsx` 未使用）

`GET /api/submissions/{id}` 返回服务器文件存储路径（如 `./uploads/xxxx.pdf`），属信息泄露。

### Bug 3：`completed_at` 用 naive datetime

- [marking.py:110](file:///Users/mac/Desktop/AI-Marking/backend/app/services/marking.py#L110)：`completed_at=datetime.now()`（naive 本地时间）
- [models/submission.py:50](file:///Users/mac/Desktop/AI-Marking/backend/app/models/submission.py#L50)：`default=datetime.now`（naive 本地时间）
- [models/system_config.py:31-32](file:///Users/mac/Desktop/AI-Marking/backend/app/models/system_config.py#L31-L32)：`default=datetime.now`、`onupdate=datetime.now`（naive）
- 对比 [health.py:15](file:///Users/mac/Desktop/AI-Marking/backend/app/api/health.py#L15)：`datetime.now(timezone.utc)`（正确）

不同时区的服务器会产生不一致时间戳。统一改为 UTC。

### README 过时

[README.md](file:///Users/mac/Desktop/AI-Marking/README.md) 仍写"百度 OCR API"和"豆包(Doubao)Ark API"，实际已是 PaddleOCR-VL + 通用 OpenAI 兼容协议。仅 31 行，缺环境变量、迁移命令、测试说明。

### 测试完全缺失

`pyproject.toml` 已配置 `testpaths=["tests"]`、`asyncio_mode="auto"`，dev 依赖含 pytest + pytest-asyncio，但 `backend/tests/` 目录不存在。

---

## 改动计划

### 3.1 Bug 1：修复 `details` 类型标注

**文件 1**：[backend/app/models/submission.py](file:///Users/mac/Desktop/AI-Marking/backend/app/models/submission.py#L46)

```python
# 第 46 行
# 改前: details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
# 改后:
details: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

**文件 2**：[backend/app/schemas/submission.py](file:///Users/mac/Desktop/AI-Marking/backend/app/schemas/submission.py#L31)

```python
# 第 31 行
# 改前: details: dict | None = None
# 改后:
details: list[dict] | None = None
```

### 3.2 Bug 2：移除 `file_path` 泄露

**文件 1**：[backend/app/schemas/submission.py](file:///Users/mac/Desktop/AI-Marking/backend/app/schemas/submission.py#L33)

删除 `SubmissionDetail` 中的 `file_path` 字段：

```python
# 第 33 行，删除:
file_path: str | None = None
```

**文件 2**：[frontend/src/api/submissions.ts](file:///Users/mac/Desktop/AI-Marking/frontend/src/api/submissions.ts#L33)

删除 `SubmissionDetail` 接口中的 `file_path` 字段：

```typescript
// 第 33 行，删除:
file_path: string;
```

### 3.3 Bug 3：统一使用 UTC 时间

**文件 1**：[backend/app/services/marking.py](file:///Users/mac/Desktop/AI-Marking/backend/app/services/marking.py)

```python
# 第 11 行 import
# 改前: from datetime import datetime
# 改后:
from datetime import datetime, timezone

# 第 110 行
# 改前: completed_at=datetime.now(),
# 改后:
completed_at=datetime.now(timezone.utc),
```

**文件 2**：[backend/app/models/submission.py](file:///Users/mac/Desktop/AI-Marking/backend/app/models/submission.py)

```python
# 第 7 行 import
# 改前: from datetime import datetime
# 改后:
from datetime import datetime, timezone

# 第 50 行
# 改前: default=datetime.now,
# 改后:
default=lambda: datetime.now(timezone.utc),
```

**文件 3**：[backend/app/models/system_config.py](file:///Users/mac/Desktop/AI-Marking/backend/app/models/system_config.py)

```python
# 第 7 行 import
# 改前: from datetime import datetime
# 改后:
from datetime import datetime, timezone

# 第 31 行
# 改前: default=datetime.now,
# 改后:
default=lambda: datetime.now(timezone.utc),

# 第 32 行
# 改前: onupdate=datetime.now,
# 改后:
onupdate=lambda: datetime.now(timezone.utc),
```

### 3.4 更新 README.md

**文件**：[README.md](file:///Users/mac/Desktop/AI-Marking/README.md)

完整重写，内容包含：

1. **项目简介**：AI 作业批改系统（SURF-2026-0031）
2. **技术栈表格**（更正）：
   - 前端：React + TypeScript + Vite + Ant Design
   - 后端：Python + FastAPI + SQLAlchemy 2.0 + Alembic
   - 数据库：PostgreSQL
   - OCR：PaddleOCR-VL（文档解析，输出结构化 Markdown）
   - LLM：OpenAI 兼容协议（支持豆包/通义/DeepSeek/OpenAI/Kimi 等，默认豆包）
3. **环境要求**：Python ≥3.10、Node ≥18、PostgreSQL ≥14
4. **快速启动**：
   - 后端：`cd backend && pip install -e ".[dev]"` → `alembic upgrade head` → `uvicorn app.main:app --reload`
   - 前端：`cd frontend && npm install && npm run dev`
5. **环境变量**：列出 `.env` 的 3 个变量（`DATABASE_URL`、`CORS_ORIGINS`、`UPLOAD_DIR`）
6. **API 配置说明**：LLM/OCR Key 通过设置页面配置（存数据库，非 .env）
7. **开发命令**：lint、format、test

### 3.5 补充后端最小测试

创建 `backend/tests/` 目录，含 4 个文件：

#### 文件 1：`backend/tests/__init__.py`

空文件，标记 Python 包。

#### 文件 2：`backend/tests/conftest.py`

测试基础设施：

```python
"""测试公共 fixtures。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.config import invalidate_config_cache


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """每个测试前后清除配置缓存,避免测试间互相污染。"""
    invalidate_config_cache()
    yield
    invalidate_config_cache()


@pytest.fixture
def db_session():
    """SQLite 内存测试数据库,每个测试独立。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session: Session):
    """FastAPI TestClient,数据库依赖注入覆盖为测试 SQLite。"""
    app = create_app()

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

#### 文件 3：`backend/tests/test_health.py`

```python
"""健康检查接口测试。"""


def test_health_returns_ok(client):
    """GET /api/health 返回 200 且 status=ok。"""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
```

#### 文件 4：`backend/tests/test_config_api.py`

```python
"""系统配置接口测试。"""


def test_get_config_returns_empty_defaults(client):
    """GET /api/config 在无配置时返回空字符串。"""
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["llm_api_key"] == ""
    assert data["llm_base_url"] == ""
    assert data["llm_model"] == ""
    assert data["paddleocr_api_url"] == ""
    assert data["paddleocr_token"] == ""
    assert data["rubric"] == ""


def test_put_config_updates_subset(client):
    """PUT /api/config 子集更新,其他字段保持不变。"""
    # 先写入两个值
    client.put("/api/config", json={"llm_api_key": "sk-001", "llm_model": "gpt-4o"})
    # 再更新其中一个
    response = client.put("/api/config", json={"llm_api_key": "sk-002"})
    assert response.status_code == 200
    data = response.json()
    assert data["llm_api_key"] == "sk-002"
    assert data["llm_model"] == "gpt-4o"  # 保持不变


def test_put_config_rejects_unknown_key(client):
    """PUT /api/config 未知 key 返回 422(extra=forbid)。"""
    response = client.put("/api/config", json={"unknown_key": "value"})
    assert response.status_code == 422


def test_put_config_empty_string_clears_value(client):
    """PUT /api/config 空字符串表示清空。"""
    client.put("/api/config", json={"llm_api_key": "sk-001"})
    response = client.put("/api/config", json={"llm_api_key": ""})
    assert response.status_code == 200
    assert response.json()["llm_api_key"] == ""
```

#### 文件 5：`backend/tests/test_submissions_api.py`

```python
"""Submission 接口测试:上传校验、列表、详情。"""

from app.models.submission import Submission, SubmissionStatus


def test_upload_non_pdf_returns_400(client):
    """POST /api/submissions 非 PDF 文件返回 400。"""
    response = client.post(
        "/api/submissions",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_get_nonexistent_submission_returns_404(client):
    """GET /api/submissions/999 不存在的 ID 返回 404。"""
    response = client.get("/api/submissions/999")
    assert response.status_code == 404


def test_get_submission_list_empty(client):
    """GET /api/submissions 空列表返回 200。"""
    response = client.get("/api/submissions")
    assert response.status_code == 200
    assert response.json() == []


def test_get_submission_detail_with_list_details(client, db_session):
    """回归测试:details 为数组类型时能正确序列化(Bug 1)。"""
    sub = Submission(
        original_filename="test.pdf",
        file_path="/tmp/test.pdf",
        status=SubmissionStatus.done,
        ocr_text="作业内容",
        score=85.0,
        feedback="整体不错",
        details=[
            {"criterion": "内容理解", "score": 25, "comment": "理解准确"},
            {"criterion": "论证分析", "score": 28, "comment": "逻辑清晰"},
        ],
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

    response = client.get(f"/api/submissions/{sub.id}")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["details"], list)
    assert len(data["details"]) == 2
    assert data["details"][0]["criterion"] == "内容理解"
    assert "file_path" not in data  # Bug 2: file_path 不应泄露
```

---

## 假设与决策

| 决策 | 理由 |
|------|------|
| `details` 改为 `list[dict]` 而非 `list[DetailItem]` | 后端 schema 保持简单，前端已有 `DetailItem` 接口做前端校验 |
| 不新增 Alembic 迁移 | 3 个 Bug 均为应用层改动，不影响 DB schema |
| 测试用 SQLite 内存数据库 | 快速、无外部依赖；`JSON` 和 `Enum` 类型在 SQLite 下可正常工作 |
| 不测试 PDF 上传成功流程 | 会触发 BackgroundTask（OCR/LLM），需 mock；"最小测试"不覆盖此场景 |
| `details` 回归测试直接写 DB | 绕过上传流程，专注验证序列化正确性 |
| 不清理 `useHealth` 死代码 | 不在用户请求范围内 |
| 不更新 `.trae/specs/` 文档 | 不在用户请求范围内 |
| 前端测试暂不补 | 需引入 vitest + @testing-library，超出"最小测试"范围 |

---

## 验证步骤

1. **Bug 1 验证**：`test_get_submission_detail_with_list_details` 测试通过（修复前会失败，修复后通过）
2. **Bug 2 验证**：同一测试断言 `"file_path" not in data`
3. **Bug 3 验证**：`grep -r "datetime.now()" backend/app/` 应无结果（全部改为 `datetime.now(timezone.utc)`）
4. **测试全部通过**：`cd backend && pytest -v`
5. **Lint 通过**：`cd backend && ruff check app/ tests/ alembic/ && black --check app/ tests/ alembic/`
6. **前端类型检查**：`cd frontend && npx tsc --noEmit`
7. **README 内容核实**：技术栈表格不再出现"百度 OCR"或仅写"豆包"
