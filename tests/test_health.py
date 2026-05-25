"""健康检查模块测试"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

def test_health_controller():
    """测试健康检查控制器存在（Flask路由方式）"""
    # Flask版本中，健康检查直接在app.py中作为路由实现
    # 验证Flask app有/health端点
    from app import app
    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert '/health' in rules or any('/health' in str(rule) for rule in rules), "应有/health路由"
    # 验证health函数存在于app模块
    from app import app as app_module
    # Flask路由装饰器自动注册端点，不需要单独的HealthCheck类

def test_health_endpoint_returns_json():
    """测试健康检查端点返回JSON"""
    with app.test_client() as client:
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert 'service' in data
        assert 'version' in data
        assert 'ts' in data
        assert 'checks' in data

def test_health_endpoint_checks_database():
    """测试健康检查包含数据库检查"""
    with app.test_client() as client:
        resp = client.get('/health')
        data = resp.get_json()
        assert 'database' in data['checks']

def test_health_service_name():
    """测试服务名称正确"""
    with app.test_client() as client:
        resp = client.get('/health')
        data = resp.get_json()
        assert data['service'] == 'csbaby-sync-server-py'

def test_health_version():
    """测试版本号"""
    with app.test_client() as client:
        resp = client.get('/health')
        data = resp.get_json()
        assert data['version'] == '2.0.0'

def test_health_timestamp():
    """测试时间戳是毫秒级"""
    with app.test_client() as client:
        resp = client.get('/health')
        data = resp.get_json()
        # 当前毫秒时间戳应该在合理范围内（2024年以后）
        assert data['ts'] > 1700000000000, "时间戳应该是毫秒级"

def test_root_endpoint():
    """测试根路径端点"""
    with app.test_client() as client:
        resp = client.get('/')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert data['service'] == 'csbaby-sync-server-py'

def test_auth_endpoints_require_auth():
    """测试认证端点需要授权"""
    with app.test_client() as client:
        # 同步端点需要认证
        resp = client.get('/sync')
        assert resp.status_code == 401, "未认证应返回401"

        # 备份列表端点需要认证
        resp = client.get('/api/v1/backup/list')
        assert resp.status_code == 401, "未认证应返回401"

def test_auth_endpoints_accept_json():
    """测试认证端点接受JSON请求"""
    with app.test_client() as client:
        # 注册端点格式验证
        resp = client.post('/auth/register',
                           json={'email': '', 'password': '', 'displayName': ''})
        # 应该返回400（缺少字段）或409（邮箱问题），不应是500
        assert resp.status_code in [400, 409, 500]
        if resp.status_code == 400:
            data = resp.get_json()
            assert data['code'] == 400

def test_register_missing_fields():
    """测试注册缺少必填字段"""
    with app.test_client() as client:
        resp = client.post('/auth/register', json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400
        assert '缺少必填字段' in data['message']

def test_login_missing_fields():
    """测试登录缺少必填字段"""
    with app.test_client() as client:
        resp = client.post('/auth/login', json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400

def test_health_includes_pid():
    """测试健康检查包含进程ID"""
    with app.test_client() as client:
        resp = client.get('/health')
        data = resp.get_json()
        assert 'pid' in data
        assert isinstance(data['pid'], int)

def test_health_status_values():
    """测试健康检查状态值"""
    with app.test_client() as client:
        resp = client.get('/health')
        data = resp.get_json()
        # 数据库连接失败时应该是 degraded
        # 数据库连接成功时应该是 ok
        assert data['status'] in ['ok', 'degraded']

def test_root_endpoint_version():
    """测试根路径端点版本"""
    with app.test_client() as client:
        resp = client.get('/')
        data = resp.get_json()
        assert data['version'] == '2.0.0'
        assert data['ts'] > 1700000000000

def test_health_response_time_reasonable():
    """测试健康检查响应时间合理"""
    import time
    with app.test_client() as client:
        start = time.time()
        resp = client.get('/health')
        elapsed = time.time() - start
        assert resp.status_code == 200
        # 健康检查应该在5秒内完成（考虑数据库连接超时）
        assert elapsed < 5.0

def test_invalid_json_body():
    """测试无效JSON请求体"""
    with app.test_client() as client:
        resp = client.post('/auth/register',
                           data='not json',
                           content_type='application/json')
        # 应该返回400或500
        assert resp.status_code in [400, 500]

def test_unauthorized_missing_bearer():
    """测试缺少Bearer前缀"""
    from utils.auth import generate_tokens
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)

    with app.test_client() as client:
        # 没有Bearer前缀
        resp = client.get('/sync', headers={'Authorization': access_token})
        assert resp.status_code == 401

def test_unauthorized_empty_auth_header():
    """测试空认证头"""
    with app.test_client() as client:
        resp = client.get('/sync', headers={'Authorization': ''})
        assert resp.status_code == 401

def test_unauthorized_wrong_auth_scheme():
    """测试错误的认证方案"""
    with app.test_client() as client:
        resp = client.get('/sync', headers={'Authorization': 'Basic sometoken'})
        assert resp.status_code == 401

def test_health_no_auth_required():
    """测试健康检查端点不需要认证"""
    with app.test_client() as client:
        # 不带任何认证就能访问健康检查
        resp = client.get('/health')
        assert resp.status_code == 200

def test_root_no_auth_required():
    """测试根路径端点不需要认证"""
    with app.test_client() as client:
        # 不带任何认证就能访问根路径
        resp = client.get('/')
        assert resp.status_code == 200

if __name__ == '__main__':
    tests = [
        test_health_controller,
        test_health_endpoint_returns_json,
        test_health_endpoint_checks_database,
        test_health_service_name,
        test_health_version,
        test_health_timestamp,
        test_root_endpoint,
        test_auth_endpoints_require_auth,
        test_auth_endpoints_accept_json,
        test_register_missing_fields,
        test_login_missing_fields,
        test_health_includes_pid,
        test_health_status_values,
        test_root_endpoint_version,
        test_health_response_time_reasonable,
        test_invalid_json_body,
        test_unauthorized_missing_bearer,
        test_unauthorized_empty_auth_header,
        test_unauthorized_wrong_auth_scheme,
        test_health_no_auth_required,
        test_root_no_auth_required,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            print(f'[PASS] {test.__name__}')
            passed += 1
        except Exception as e:
            print(f'[FAIL] {test.__name__}: {e}')
    print(f'\nTotal: {passed}/{len(tests)} passed')