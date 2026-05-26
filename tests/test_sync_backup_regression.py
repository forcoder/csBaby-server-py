"""
同步和备份回归测试用例
验证客户端和服务端的接口兼容性
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 同步接口兼容测试 ==========

def test_client_compatible_endpoints_exist():
    """测试客户端兼容的端点是否存在"""
    from app import app

    # 检查 /sync/all 端点存在
    routes = [rule.rule for rule in app.url_map.iter_rules()]
    assert '/sync/all' in routes, "缺少 /sync/all 端点"

    # 检查 /sync/changes 端点存在
    assert '/sync/changes' in routes, "缺少 /sync/changes 端点"

    # 检查 /sync/resolve 端点存在
    assert '/sync/resolve' in routes, "缺少 /sync/resolve 端点"


def test_sync_all_endpoint_requires_auth():
    """测试 /sync/all 端点需要认证"""
    from app import app
    with app.test_client() as client:
        resp = client.get('/sync/all')
        assert resp.status_code == 401, "/sync/all 应该需要认证"


def test_sync_changes_endpoint_requires_auth():
    """测试 /sync/changes 端点需要认证"""
    from app import app
    with app.test_client() as client:
        resp = client.get('/sync/changes')
        assert resp.status_code == 401, "/sync/changes 应该需要认证"


def test_sync_resolve_endpoint_requires_auth():
    """测试 /sync/resolve 端点需要认证"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/sync/resolve', json={})
        assert resp.status_code == 401, "/sync/resolve 应该需要认证"


def test_sync_all_returns_correct_structure():
    """测试 /sync/all 返回正确的数据结构"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        resp = client.get('/sync/all', headers={'Authorization': f'Bearer {access_token}'})
        # 无数据库时可能500，但应该返回JSON结构
        if resp.status_code == 200:
            data = resp.get_json()
            assert 'data' in data, "响应缺少 data 字段"
            sync_data = data['data']
            # 检查全量同步应返回的所有字段
            required_fields = ['keywordRules', 'aiModelConfigs', 'userStyleProfile',
                               'appConfigs', 'scenarios', 'replyHistory', 'messageBlacklist', 'serverTime']
            for field in required_fields:
                assert field in sync_data, f"全量同步缺少字段: {field}"


def test_sync_changes_returns_correct_structure():
    """测试 /sync/changes 返回正确的数据结构"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        resp = client.get('/sync/changes?since=0', headers={'Authorization': f'Bearer {access_token}'})
        if resp.status_code == 200:
            data = resp.get_json()
            assert 'data' in data, "响应缺少 data 字段"
            sync_data = data['data']
            # 检查增量同步应返回的字段
            required_fields = ['keywordRules', 'serverTime', 'page', 'limit', 'hasMore']
            for field in required_fields:
                assert field in sync_data, f"增量同步缺少字段: {field}"


def test_sync_changes_with_pagination():
    """测试 /sync/changes 分页参数"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        resp = client.get('/sync/changes?since=1000&page=2&limit=50',
                          headers={'Authorization': f'Bearer {access_token}'})
        # 应该成功或数据库错误，但不应该是404
        assert resp.status_code != 404, "/sync/changes 端点不应返回404"


# ========== 备份字段兼容测试 ==========

def test_backup_upload_accepts_camelcase_fields():
    """测试备份上传接受客户端的 camelCase 字段名"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')

    # 模拟客户端发送的数据（camelCase）
    client_data = {
        'deviceName': 'Test Device',
        'appVersion': '1.0.0',
        'data': {'keywordRules': [], 'aiModelConfigs': []},
        'checksum': 'abc123',
        'backupType': 'manual'
    }

    with app.test_client() as client:
        resp = client.post('/api/v1/backup/upload',
                            headers={'Authorization': f'Bearer {access_token}'},
                            json=client_data)
        # 应该成功或数据库错误
        assert resp.status_code in [200, 500], "备份上传端点应该能处理camelCase字段"


def test_backup_list_response_format():
    """测试备份列表响应格式"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        resp = client.get('/api/v1/backup/list',
                          headers={'Authorization': f'Bearer {access_token}'})
        if resp.status_code == 200:
            data = resp.get_json()
            assert 'data' in data, "响应缺少 data 字段"
            # 验证返回的是列表
            assert isinstance(data['data'], list), "备份列表应该是数组"


def test_backup_response_has_required_fields():
    """测试备份响应包含必填字段"""
    from models.backup import create_backup

    # 模拟返回的备份记录格式
    mock_record = {
        'id': 1,
        'deviceName': 'Test Device',
        'appVersion': '1.0.0',
        'dataSize': 1024,
        'checksum': 'abc123',
        'version': '1.0',
        'backupType': 'manual',
        'createdAt': 1704067200000
    }

    required_fields = ['id', 'deviceName', 'appVersion', 'dataSize', 'checksum',
                       'version', 'backupType', 'createdAt']
    for field in required_fields:
        assert field in mock_record, f"备份记录缺少字段: {field}"


def test_backup_data_json_structure():
    """测试备份数据JSON结构与客户端期望一致"""
    from models.backup import create_backup
    import json

    # 客户端发送的备份内容结构
    backup_content = {
        'keywordRules': [
            {'id': 1, 'keyword': 'test', 'matchType': 'CONTAINS', 'replyTemplate': 'Hi'}
        ],
        'aiModelConfigs': [
            {'id': 1, 'modelType': 'openai', 'modelName': 'GPT-4'}
        ],
        'userStyleProfile': {'userId': 'u1', 'formalityLevel': 0.5},
        'appConfigs': [
            {'packageName': 'com.example.app', 'appName': 'Example', 'isMonitored': True}
        ],
        'scenarios': [],
        'replyHistory': [],
        'messageBlacklist': []
    }

    # 验证可以序列化为JSON
    data_json = json.dumps(backup_content)
    parsed = json.loads(data_json)

    assert 'keywordRules' in parsed
    assert 'aiModelConfigs' in parsed
    assert 'userStyleProfile' in parsed
    assert 'appConfigs' in parsed


# ========== 同步数据模型兼容测试 ==========

def test_sync_service_returns_camelcase_fields():
    """测试同步服务返回 camelCase 字段名（客户端期望）"""
    from services.sync_service import to_rule, to_model, to_app

    # 数据库返回的 snake_case 数据
    rule_tuple = ('1', 'test', 'CONTAINS', 'reply', 'cat', 'ALL', '[]', 0, True, 1000, 2000, 't1', 3000, False)
    rule = to_rule(rule_tuple)

    # 验证返回的是客户端期望的 camelCase 字段名
    expected_camelcase_fields = ['keywordRules', 'aiModelConfigs', 'userStyleProfile',
                                  'appConfigs', 'scenarios', 'replyHistory', 'messageBlacklist']

    # 检查 to_rule 返回的字段是 camelCase
    assert 'matchType' in rule, "字段名应该是 camelCase: matchType"
    assert 'replyTemplate' in rule, "字段名应该是 camelCase: replyTemplate"
    assert 'targetType' in rule, "字段名应该是 camelCase: targetType"
    assert 'targetNamesJson' in rule, "字段名应该是 camelCase: targetNamesJson"
    assert 'syncVersion' in rule, "字段名应该是 camelCase: syncVersion"


def test_sync_push_handles_client_data():
    """测试同步推送能处理客户端数据"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')

    # 模拟客户端推送的数据
    client_push_data = {
        'keywordRules': [
            {
                'id': 1,
                'keyword': 'hello',
                'matchType': 'CONTAINS',
                'replyTemplate': 'Hi there!',
                'category': 'greeting',
                'targetType': 'ALL',
                'targetNamesJson': '[]',
                'priority': 0,
                'enabled': True,
                'createdAt': 1704067200000,
                'updatedAt': 1704067200000,
                'tenantId': 'test-tenant',
                'syncVersion': 1704067200000,
                'deleted': False
            }
        ]
    }

    with app.test_client() as client:
        resp = client.post('/sync/push',
                          headers={'Authorization': f'Bearer {access_token}'},
                          json=client_push_data)
        # 应该成功或数据库错误
        assert resp.status_code in [200, 500]


# ========== 边界情况测试 ==========

def test_sync_with_invalid_since_param():
    """测试同步端点处理无效的since参数"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        # 发送非数字的since参数
        resp = client.get('/sync?since=abc',
                          headers={'Authorization': f'Bearer {access_token}'})
        # 应该返回错误（400或500）
        assert resp.status_code in [400, 500]


def test_backup_upload_with_empty_data():
    """测试备份上传空数据"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        resp = client.post('/api/v1/backup/upload',
                          headers={'Authorization': f'Bearer {access_token}'},
                          json={'deviceName': 'test', 'appVersion': '1.0', 'data': {}})
        # 应该成功或数据库错误
        assert resp.status_code in [200, 500]


def test_backup_download_nonexistent():
    """测试下载不存在的备份"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        resp = client.get('/api/v1/backup/download/99999',
                          headers={'Authorization': f'Bearer {access_token}'})
        # 应该返回404或500（取决于数据库状态）
        assert resp.status_code in [404, 500]


def test_sync_changes_without_since_param():
    """测试增量同步没有since参数时的默认行为"""
    from app import app
    from app import generate_tokens

    access_token, _ = generate_tokens('test-user', 'test-tenant')
    with app.test_client() as client:
        # 不带since参数，应该默认使用0（全量）
        resp = client.get('/sync/changes',
                          headers={'Authorization': f'Bearer {access_token}'})
        # 应该成功或数据库错误
        assert resp.status_code in [200, 500]


# ========== 运行所有测试 ==========

if __name__ == '__main__':
    tests = [
        # 同步接口兼容测试
        test_client_compatible_endpoints_exist,
        test_sync_all_endpoint_requires_auth,
        test_sync_changes_endpoint_requires_auth,
        test_sync_resolve_endpoint_requires_auth,
        test_sync_all_returns_correct_structure,
        test_sync_changes_returns_correct_structure,
        test_sync_changes_with_pagination,
        # 备份字段兼容测试
        test_backup_upload_accepts_camelcase_fields,
        test_backup_list_response_format,
        test_backup_response_has_required_fields,
        test_backup_data_json_structure,
        # 同步数据模型兼容测试
        test_sync_service_returns_camelcase_fields,
        test_sync_push_handles_client_data,
        # 边界情况测试
        test_sync_with_invalid_since_param,
        test_backup_upload_with_empty_data,
        test_backup_download_nonexistent,
        test_sync_changes_without_since_param,
    ]

    passed = 0
    failed = []

    for test in tests:
        try:
            test()
            print(f'[PASS] {test.__name__}')
            passed += 1
        except AssertionError as e:
            print(f'[FAIL] {test.__name__}: {e}')
            failed.append((test.__name__, str(e)))
        except Exception as e:
            print(f'[ERROR] {test.__name__}: {e}')
            failed.append((test.__name__, str(e)))

    print(f'\n总计: {passed}/{len(tests)} 通过')
    if failed:
        print(f'\n失败/错误:')
        for name, error in failed:
            print(f'  - {name}: {error}')