"""系统配置接口测试。"""


async def test_get_config_returns_empty_defaults(client):
    """GET /api/config 在无配置时返回空字符串。"""
    response = await client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["paddleocr_api_url"] == ""
    assert data["paddleocr_token"] == ""
    assert data["rubric_definition"] is None
    assert data["review_enabled"] is True
    assert "llm_api_key" not in data
    assert "llm_user_prompt" not in data


async def test_put_config_updates_subset(client):
    """PUT /api/config 子集更新,其他字段保持不变。"""
    await client.put(
        "/api/config",
        json={"paddleocr_api_url": "https://ocr.example/jobs", "paddleocr_token": "t1"},
    )
    response = await client.put(
        "/api/config", json={"paddleocr_token": "t2"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["paddleocr_token"] == "t2"
    assert data["paddleocr_api_url"] == "https://ocr.example/jobs"  # 保持不变


async def test_put_config_updates_review_enabled(client):
    """review_enabled 布尔开关可更新。"""
    response = await client.put("/api/config", json={"review_enabled": False})
    assert response.status_code == 200
    assert response.json()["review_enabled"] is False


async def test_put_config_rejects_unknown_key(client):
    """PUT /api/config 未知 key 返回 422(extra=forbid)。"""
    response = await client.put("/api/config", json={"unknown_key": "value"})
    assert response.status_code == 422
    # 已删除的 LLM 配置 key 同样被拒绝
    response = await client.put("/api/config", json={"llm_api_key": "sk-001"})
    assert response.status_code == 422


async def test_put_config_empty_string_clears_value(client):
    """PUT /api/config 空字符串表示清空。"""
    await client.put("/api/config", json={"paddleocr_token": "sk-001"})
    response = await client.put("/api/config", json={"paddleocr_token": ""})
    assert response.status_code == 200
    assert response.json()["paddleocr_token"] == ""


# ---------------- 配置项目 CRUD ----------------


async def test_profiles_default_seeded(client):
    """测试库自动创建默认配置项目。"""
    response = await client.get("/api/config/profiles")
    assert response.status_code == 200
    profiles = response.json()
    assert any(p["name"] == "默认配置" and p["is_default"] for p in profiles)


async def test_create_profile_and_config_is_scoped(client):
    """新建项目后,GET/PUT config 按 profile_id 隔离。"""
    # 默认项目写入值
    await client.put("/api/config", json={"paddleocr_token": "sk-default"})

    created = await client.post("/api/config/profiles", json={"name": "项目A"})
    assert created.status_code == 201
    profile_id = created.json()["id"]

    # 新项目为空,不影响默认项目
    resp_a = await client.get(f"/api/config?profile_id={profile_id}")
    assert resp_a.json()["paddleocr_token"] == ""
    resp_default = await client.get("/api/config")
    assert resp_default.json()["paddleocr_token"] == "sk-default"

    # 向 A 写入,默认项目保持不变
    resp_a2 = await client.put(
        f"/api/config?profile_id={profile_id}",
        json={"paddleocr_token": "sk-A"},
    )
    assert resp_a2.json()["paddleocr_token"] == "sk-A"
    resp_default2 = await client.get("/api/config")
    assert resp_default2.json()["paddleocr_token"] == "sk-default"


async def test_create_profile_rejects_duplicate_name(client):
    await client.post("/api/config/profiles", json={"name": "重复项目"})
    response = await client.post("/api/config/profiles", json={"name": "重复项目"})
    assert response.status_code == 400


async def test_copy_profile_copies_config_values(client, db_session):
    """复制生成的项目携带源项目的全部配置值。"""
    rubric = {"items": [{"criterion": "内容", "max_score": 100, "details": "评分细则"}], "total_max_score": 100}
    await client.put(
        "/api/config",
        json={"paddleocr_token": "sk-001", "rubric_definition": rubric},
    )
    created = await client.post(
        "/api/config/profiles",
        json={"name": "复制版", "copy_from_id": _default_profile_id(db_session)},
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    data = (await client.get(f"/api/config?profile_id={pid}")).json()
    assert data["paddleocr_token"] == "sk-001"
    assert data["rubric_definition"]["items"][0]["criterion"] == "内容"


async def test_rename_profile(client):
    created = await client.post("/api/config/profiles", json={"name": "旧名"})
    pid = created.json()["id"]
    renamed = await client.patch(f"/api/config/profiles/{pid}", json={"name": "新名"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "新名"
    profiles = (await client.get("/api/config/profiles")).json()
    assert any(p["name"] == "新名" for p in profiles)
    assert not any(p["name"] == "旧名" for p in profiles)


async def test_delete_referenced_profile_rejected(client, db_session):
    """被题目引用的配置项目不可删除。"""
    default_id = _default_profile_id(db_session)
    created = await client.post("/api/config/profiles", json={"name": "被引用项目"})
    pid = created.json()["id"]
    # 创建题目绑定到该项目
    from app.models.question import Question, QuestionStatus

    q = Question(
        name="绑定题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        status=QuestionStatus.ready,
        config_profile_id=pid,
    )
    db_session.add(q)
    db_session.commit()

    response = await client.delete(f"/api/config/profiles/{pid}")
    assert response.status_code == 400
    assert f"{pid}" in str(response.json()["detail"]) or "引用" in str(response.json())

    # 默认项目同样不可删
    default_resp = await client.delete(f"/api/config/profiles/{default_id}")
    assert default_resp.status_code == 400


async def test_delete_orphan_profile_ok(client, db_session):
    """未被题目引用的非默认项目可删除。"""
    created = await client.post("/api/config/profiles", json={"name": "孤儿项目"})
    pid = created.json()["id"]
    response = await client.delete(f"/api/config/profiles/{pid}")
    assert response.status_code in (200, 204)
    profiles = (await client.get("/api/config/profiles")).json()
    assert not any(p["id"] == pid for p in profiles)


def test_default_profile_lazily_created(db_session):
    """真正空的数据库读取配置时自动创建默认项目,不会抛错。"""
    from sqlalchemy import text

    from app.models.config_profile import ConfigProfile

    db_session.execute(text("DELETE FROM config_profiles"))
    db_session.commit()
    # 通过默认路径读取配置(profile_id=None),应自动补默认项目
    from app.services.config import get_config_dict

    cfg = get_config_dict(db_session)
    assert cfg == {}
    # 已存在默认项目
    got = db_session.query(ConfigProfile).filter_by(is_default=True).first()
    assert got is not None


def _default_profile_id(db_session):
    from app.models.config_profile import ConfigProfile

    return db_session.query(ConfigProfile).filter_by(is_default=True).one().id
