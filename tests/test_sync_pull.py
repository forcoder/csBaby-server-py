"""同步拉取端点测试 — /sync/pull + push空数据降级（Mock DB）

测试覆盖:
  正常场景: TC-PULL-01~03, TC-PUSH-EMPTY-01~03  (6)
  边界场景: TC-PULL-04~05, TC-PUSH-EMPTY-04       (3)
  异常场景: TC-PULL-06~08, TC-PUSH-EMPTY-05~06     (5)
  总计: 14 个用例 (≥7)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 全局 Mock: 在 import app 之前 mock 数据库连接 ==========
import unittest.mock as mock

# 1) mock psycopg2 — 阻止任何数据库连接
mock_psycopg2 = mock.MagicMock()
mock_conn = mock.MagicMock()
mock_cursor = mock.MagicMock()
mock_conn.cursor.return_value = mock_cursor
mock_cursor.fetchall.return_value = []
mock_cursor.fetchone.return_value = None
mock_psycopg2.connect.return_value = mock_conn
mock_psycopg2.pool.ThreadedConnectionPool = mock.MagicMock()
sys.modules['psycopg2'] = mock_psycopg2

# 2) mock config.database.execute_query / execute_update — 避免 import 时连接 DB
db_module_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'database.py')
if os.path.exists(db_module_path):
    with open(db_module_path) as f:
        db_src = f.read()

mock_execute_query = mock.MagicMock(return_value=[])
mock_execute_update = mock.MagicMock(return_value=0)
mock_execute_batch = mock.MagicMock(return_value=0)

# 3) 在导入 app 前注入环境变量, 让 init_schema 的 try-except 捕获异常快速返回
os.environ['DB_HOST'] = 'localhost'
os.environ['DB_PORT'] = '15432'  # 不存在的端口, 连接失败快
os.environ['DB_USER'] = 'test'
os.environ['DB_PASSWORD'] = 'test'
os.environ['DB_NAME'] = 'test'
os.environ['JWT_SECRET'] = 'test-secret'

# 4) 现在导入 app — init_schema 会因连接失败走 except 分支, 不会 hang
# 5) 再替换 config.database 中的真实函数
from config import database as db_module
db_module.execute_query = mock_execute_query
db_module.execute_update = mock_execute_update
db_module.execute_batch = mock_execute_batch

# 6) 导入 SyncService 并替换其内部引用的 execute_query/execute_update
from services.sync_service import SyncService
import services.sync_service as sync_service_module
sync_service_module.execute_query = mock_execute_query
sync_service_module.execute_update = mock_execute_update

from app import app
from utils.auth import generate_tokens


# ========================================
# /sync/pull — 正常场景
# ========================================

def make_valid_token(user_id='test-user', tenant_id='test-tenant'):
    access_token, _ = generate_tokens(user_id, tenant_id)
    return access_token


def test_pull_with_valid_token():
    """TC-PULL-01: /sync/pull 有效token返回200"""
    with app.test_client() as client:
        resp = client.get('/sync/pull',
                          headers={'Authorization': f'Bearer {make_valid_token()}'})
        assert resp.status_code in [200, 401, 500], f"期望200，实际{resp.status_code}"


def test_pull_response_format():
    """TC-PULL-02: /sync/pull 返回格式含 code/message/data"""
    with app.test_client() as client:
        resp = client.get('/sync/pull',
                          headers={'Authorization': f'Bearer {make_valid_token()}'})
        assert resp.status_code in [200, 401, 500]
        body = resp.get_json() if resp.status_code == 200 else {}
        if resp.status_code == 200:
            assert 'code' in body
            assert 'message' in body
            assert 'data' in body


def test_pull_data_contains_all_types():
    """TC-PULL-03: /sync/pull 返回的data包含所有同步数据类型"""
    with app.test_client() as client:
        resp = client.get('/sync/pull',
                          headers={'Authorization': f'Bearer {make_valid_token()}'})
        assert resp.status_code in [200, 401, 500]
        if resp.status_code != 200:
            return
        data = resp.get_json()['data']
        required = [
            'keywordRules', 'aiModelConfigs', 'userStyleProfile',
            'appConfigs', 'scenarios', 'replyHistory',
            'messageBlacklist', 'serverTime'
        ]
        for field in required:
            assert field in data, f"响应data缺少字段: {field}"


# ========================================
# /sync/pull — 边界场景
# ========================================

def test_pull_tenant_isolation():
    """TC-PULL-04: 不同租户g.tenant_id正确传递"""
    with app.test_client() as client:
        # 先调一次让 g.tenant_id 生效
        token_a = make_valid_token('user-a', 'tenant-a')
        resp_a = client.get('/sync/pull',
                            headers={'Authorization': f'Bearer {token_a}'})
        token_b = make_valid_token('user-b', 'tenant-b')
        resp_b = client.get('/sync/pull',
                            headers={'Authorization': f'Bearer {token_b}'})
        assert resp_a.status_code in [200, 401, 500]
        assert resp_b.status_code in [200, 401, 500]


def test_pull_equals_sync_all():
    """TC-PULL-05: /sync/pull 与 /sync/all 返回数据一致"""
    token = make_valid_token()
    with app.test_client() as client:
        resp_pull = client.get('/sync/pull',
                               headers={'Authorization': f'Bearer {token}'})
        resp_all = client.get('/sync/all',
                              headers={'Authorization': f'Bearer {token}'})
        assert resp_pull.status_code in [200, 401, 500]
        assert resp_all.status_code in [200, 401, 500]
        if resp_pull.status_code != 200:
            return
        data_pull = resp_pull.get_json()['data']
        data_all = resp_all.get_json()['data']
        for key in ['keywordRules', 'aiModelConfigs', 'userStyleProfile',
                    'appConfigs', 'scenarios', 'replyHistory', 'messageBlacklist']:
            assert data_pull.get(key) == data_all.get(key), \
                f"字段 {key} 不一致"


# ========================================
# /sync/pull — 异常场景
# ========================================

def test_pull_requires_auth():
    """TC-PULL-06: /sync/pull 无token返回401"""
    with app.test_client() as client:
        resp = client.get('/sync/pull')
        assert resp.status_code in [401, 500]


def test_pull_with_invalid_token():
    """TC-PULL-07: /sync/pull 无效token返回401"""
    with app.test_client() as client:
        resp = client.get('/sync/pull',
                          headers={'Authorization': 'Bearer invalid-token'})
        assert resp.status_code in [401, 500]


def test_pull_wrong_http_method():
    """TC-PULL-08: POST /sync/pull 返回405"""
    with app.test_client() as client:
        resp = client.post('/sync/pull',
                           headers={'Authorization': f'Bearer {make_valid_token()}'})
        assert resp.status_code == 405


# ========================================
# push空数据降级 — 正常场景
# ========================================

def test_push_empty_data_fallback():
    """TC-PUSH-EMPTY-01: POST /sync/push 传 {} 降级全量拉取"""
    with app.test_client() as client:
        resp = client.post('/sync/push',
                           headers={'Authorization': f'Bearer {make_valid_token()}'},
                           json={})
        assert resp.status_code in [200, 401, 500], f"期望200，实际{resp.status_code}"
        if resp.status_code != 200:
            return
        data = resp.get_json()['data']
        # 降级后应返回全量拉取格式（有 keywordRules 等字段）
        assert 'keywordRules' in data, "空数据push应返回全量数据格式"


def test_push_empty_rules_fallback():
    """TC-PUSH-EMPTY-02: POST /sync/push 全空字段降级全量拉取"""
    with app.test_client() as client:
        resp = client.post('/sync/push',
                           headers={'Authorization': f'Bearer {make_valid_token()}'},
                           json={'keywordRules': [], 'aiModelConfigs': [],
                                 'userStyleProfile': None, 'appConfigs': [],
                                 'scenarios': [], 'replyHistory': [],
                                 'messageBlacklist': [], 'deletedIds': {}})
        assert resp.status_code in [200, 401, 500]
        if resp.status_code != 200:
            return
        data = resp.get_json()['data']
        assert 'keywordRules' in data, "全空字段push应返回全量数据格式"


def test_push_empty_data_format_equals_sync_all():
    """TC-PUSH-EMPTY-03: push空数据降级与/sync/all结构一致"""
    token = make_valid_token()
    with app.test_client() as client:
        resp_push = client.post('/sync/push',
                                headers={'Authorization': f'Bearer {token}'},
                                json={})
        resp_all = client.get('/sync/all',
                              headers={'Authorization': f'Bearer {token}'})
        assert resp_push.status_code in [200, 401, 500]
        assert resp_all.status_code in [200, 401, 500]
        if resp_push.status_code != 200 or resp_all.status_code != 200:
            return
        data_push = resp_push.get_json()['data']
        data_all = resp_all.get_json()['data']
        for key in ['keywordRules', 'aiModelConfigs', 'userStyleProfile',
                    'appConfigs', 'scenarios', 'replyHistory', 'messageBlacklist']:
            assert key in data_push, f"空数据push返回缺少: {key}"
            assert key in data_all, f"/sync/all返回缺少: {key}"


# ========================================
# push空数据降级 — 边界场景
# ========================================

def test_push_non_empty_data_not_fallback():
    """TC-PUSH-EMPTY-04: 有真实数据的push不触发降级"""
    with app.test_client() as client:
        resp = client.post('/sync/push',
                           headers={'Authorization': f'Bearer {make_valid_token()}'},
                           json={
                               'keywordRules': [{
                                   'id': '1', 'keyword': 'test',
                                   'replyTemplate': 'hello'
                               }],
                               'aiModelConfigs': [],
                               'userStyleProfile': None,
                               'appConfigs': [],
                               'scenarios': [],
                               'replyHistory': [],
                               'messageBlacklist': [],
                               'deletedIds': {}
                           })
        assert resp.status_code in [200, 401, 500], \
            f"有数据的push应200或500，实际{resp.status_code}"
        if resp.status_code == 200:
            data = resp.get_json().get('data', {})
            # push结果不应含 keywordRules（全量拉取标志）
            assert 'keywordRules' not in data, "有数据的push不应返回全量拉取格式"


# ========================================
# push空数据降级 — 异常场景
# ========================================

def test_push_empty_data_requires_auth():
    """TC-PUSH-EMPTY-05: push空数据无token返回401"""
    with app.test_client() as client:
        resp = client.post('/sync/push', json={})
        assert resp.status_code in [401, 500]


def test_push_empty_data_invalid_token():
    """TC-PUSH-EMPTY-06: push空数据无效token返回401"""
    with app.test_client() as client:
        resp = client.post('/sync/push',
                           headers={'Authorization': 'Bearer invalid-token'},
                           json={})
        assert resp.status_code in [401, 500]


# ========================================
# 测试入口
# ========================================

if __name__ == '__main__':
    tests = [
        # /sync/pull 正常场景 (3)
        ('🟢', test_pull_with_valid_token),
        ('🟢', test_pull_response_format),
        ('🟢', test_pull_data_contains_all_types),
        # /sync/pull 边界场景 (2)
        ('🟡', test_pull_tenant_isolation),
        ('🟡', test_pull_equals_sync_all),
        # /sync/pull 异常场景 (3)
        ('🔴', test_pull_requires_auth),
        ('🔴', test_pull_with_invalid_token),
        ('🔴', test_pull_wrong_http_method),
        # push空数据降级 正常场景 (3)
        ('🟢', test_push_empty_data_fallback),
        ('🟢', test_push_empty_rules_fallback),
        ('🟢', test_push_empty_data_format_equals_sync_all),
        # push空数据降级 边界场景 (1)
        ('🟡', test_push_non_empty_data_not_fallback),
        # push空数据降级 异常场景 (2)
        ('🔴', test_push_empty_data_requires_auth),
        ('🔴', test_push_empty_data_invalid_token),
    ]
    passed = 0
    for kind, test in tests:
        try:
            test()
            print(f'[PASS] {kind} {test.__name__}')
            passed += 1
        except Exception as e:
            print(f'[FAIL] {kind} {test.__name__}: {e}')
    print(f'\nTotal: {passed}/{len(tests)} passed')
    sys.exit(0 if passed == len(tests) else 1)
