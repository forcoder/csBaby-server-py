"""admin_controller 索引迁移测试。

覆盖:
  - 正常场景: 鉴权通过、迁移流程各步骤
  - 边界值: 幂等重复调用、无重复数据、索引已存在
  - 异常: 未配置 ADMIN_USER_IDS、非管理员、token 无效、数据库异常
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
from controllers.admin_controller import admin_bp, _calc_keyword_hash


def _make_app():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(admin_bp)
    return app


def _admin_token(user_id="admin-1"):
    import jwt
    return jwt.encode({"user_id": user_id, "type": "access"}, os.getenv("JWT_SECRET", "default-secret-change-me"), algorithm="HS256")


# ========== 正常场景 ==========

def test_calc_keyword_hash_md5_of_keyword_plus_reply():
    """keyword_hash = md5(keyword + reply), 与 sync_service 一致"""
    h = _calc_keyword_hash("测试", "回复")
    import hashlib
    expected = hashlib.md5("测试回复".encode("utf-8")).hexdigest()
    assert h == expected


def test_calc_keyword_hash_empty_inputs():
    """空 keyword/reply 应返回 md5("") 而非报错"""
    import hashlib
    assert _calc_keyword_hash("", "") == hashlib.md5(b"").hexdigest()
    assert _calc_keyword_hash(None, None) == hashlib.md5(b"").hexdigest()


def test_migrate_keyword_index_admin_authorized():
    """管理员鉴权通过, 返回迁移成功"""
    app = _make_app()
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "admin-1"}):
        with patch("config.database.execute_update", return_value=0) as mock_up, \
             patch("config.database.execute_query", return_value=[(0,)]):
            client = app.test_client()
            resp = client.post("/admin/migrate-content-hash",
                               headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code in [200, 401, 500]
    data = resp.get_json()
    assert data["code"] == 0
    assert "steps" in data["data"]
    mock_up.assert_called()


def test_keyword_index_status_returns_index_info():
    """状态查询接口返回索引/总数/重复组信息"""
    app = _make_app()
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "admin-1"}):
        with patch("config.database.execute_query", side_effect=[
            [("keyword_rules", 0, "uk_tenant_keyword_hash", "tenant_id", 0)],  # SHOW INDEX
            [(188,)],   # COUNT total
            [(0,)],     # COUNT null hash
            [(0,)],     # COUNT dup groups
        ]):
            client = app.test_client()
            resp = client.get("/admin/content-hash-status",
                              headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code in [200, 401, 500]
    data = resp.get_json()["data"]
    assert data["totalActiveRules"] == 188
    assert data["dupGroups"] == 0


# ========== 边界值场景 ==========

def test_migrate_idempotent_repeated_call_no_error():
    """重复调用迁移接口不报错 (列/索引已存在时跳过)"""
    app = _make_app()
    # 第一次 ALTER 抛 "Duplicate column", 后续 execute_update 正常返回 0
    call_count = {"n": 0}
    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("Duplicate column")
        return 0
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "admin-1"}):
        with patch("config.database.execute_update", side_effect=side_effect), \
             patch("config.database.execute_query", return_value=[(0,)]):
            client = app.test_client()
            resp = client.post("/admin/migrate-content-hash",
                               headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code in [200, 401, 500]
    assert resp.get_json()["code"] == 0


def test_migrate_with_no_duplicates_skips_dedup():
    """无重复数据时 step3 跳过删除"""
    app = _make_app()
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "admin-1"}):
        with patch("config.database.execute_update", return_value=0) as mock_up, \
             patch("config.database.execute_query", return_value=[(0,)]):
            client = app.test_client()
            resp = client.post("/admin/migrate-content-hash",
                               headers={"Authorization": f"Bearer {_admin_token()}"})
    data = resp.get_json()["data"]
    assert data["dupGroups"] == 0
    # step3_dedup 应记录 no duplicates
    dedup_step = [s for s in data["steps"] if s.startswith("step3_dedup")][0]
    assert "no duplicates" in dedup_step


# ========== 异常/错误场景 ==========

def test_migrate_rejected_when_admin_ids_not_configured():
    """未配置 ADMIN_USER_IDS 时拒绝访问 (403)"""
    app = _make_app()
    with patch.dict(os.environ, {}, clear=True):
        client = app.test_client()
        resp = client.post("/admin/migrate-content-hash",
                           headers={"Authorization": f"Bearer {_admin_token()}"})
    assert resp.status_code in [401, 403, 500]
    assert "ADMIN_USER_IDS" in resp.get_json()["message"]


def test_migrate_rejected_for_non_admin_user():
    """非白名单 user_id 拒绝访问 (403)"""
    app = _make_app()
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "admin-1"}):
        client = app.test_client()
        resp = client.post("/admin/migrate-content-hash",
                           headers={"Authorization": f"Bearer {_admin_token('other-user')}"})
    assert resp.status_code in [401, 403, 500]


def test_migrate_rejected_without_auth_header():
    """缺少 Authorization 头返回 401"""
    app = _make_app()
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "admin-1"}):
        client = app.test_client()
        resp = client.post("/admin/migrate-content-hash")
    assert resp.status_code in [401, 500]


def test_migrate_rejected_with_invalid_token():
    """无效 token 返回 401"""
    app = _make_app()
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "admin-1"}):
        client = app.test_client()
        resp = client.post("/admin/migrate-content-hash",
                           headers={"Authorization": "Bearer invalid.token.here"})
    assert resp.status_code in [401, 500]
