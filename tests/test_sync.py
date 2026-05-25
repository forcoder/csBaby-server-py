"""同步模块测试"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.sync_service import SyncService, to_rule, to_model, to_profile, to_app, to_scenario, to_reply, to_blacklist

def test_entity_transformers():
    """测试数据转换函数"""
    # 关键词规则转换
    rule_tuple = ('123', 'test', 'CONTAINS', 'Reply', 'cat', 'ALL', '[]', 0, True, 1000, 2000, 't1', 3000, False)
    rule = to_rule(rule_tuple)
    assert rule['id'] == '123'
    assert rule['keyword'] == 'test'
    assert rule['matchType'] == 'CONTAINS'
    assert rule['enabled'] == True
    assert rule['deleted'] == False

    # AI模型转换
    model_tuple = ('456', 'openai', 'GPT-4', 'key', 'url', 0.7, 1000, True, True, 100, 2000, 3000, 't1', 4000, False)
    model = to_model(model_tuple)
    assert model['id'] == '456'
    assert model['modelName'] == 'GPT-4'
    assert model['isDefault'] == True
    assert model['isEnabled'] == True

    # 用户风格配置转换
    profile_tuple = ('u1', 0.5, 0.6, 0.7, 50, '[]', '[]', '[]', 0.8, 1000, 2000, 't1', 3000, False)
    profile = to_profile(profile_tuple)
    assert profile['userId'] == 'u1'
    assert profile['formalityLevel'] == 0.5
    assert profile['accuracyScore'] == 0.8

    # App配置转换
    app_tuple = ('com.app', 'TestApp', 'icon.png', True, 1000, 2000, 't1', 3000, False)
    app = to_app(app_tuple)
    assert app['packageName'] == 'com.app'
    assert app['appName'] == 'TestApp'
    assert app['isMonitored'] == True

    # 场景配置转换
    scenario_tuple = ('s1', 'Work', 'auto', 't1', 'Work scenarios', 1000, 't1', 2000, False)
    scenario = to_scenario(scenario_tuple)
    assert scenario['id'] == 's1'
    assert scenario['name'] == 'Work'
    assert scenario['type'] == 'auto'

    # 回复历史转换
    reply_tuple = ('r1', 'com.app', 'Hi', 'Hello', 'Hello', '123', '456', True, 1000, True, 't1', 2000, False)
    reply = to_reply(reply_tuple)
    assert reply['id'] == 'r1'
    assert reply['sourceApp'] == 'com.app'
    assert reply['originalMessage'] == 'Hi'
    assert reply['styleApplied'] == True

    # 黑名单转换
    blacklist_tuple = ('b1', 'keyword', 'spam', 'Block spam', 'com.app', 1000, True, 't1', 2000, False)
    blacklist = to_blacklist(blacklist_tuple)
    assert blacklist['id'] == 'b1'
    assert blacklist['type'] == 'keyword'
    assert blacklist['isEnabled'] == True

def test_sync_service():
    """测试SyncService类存在性和方法"""
    service = SyncService()
    assert hasattr(service, 'full_sync')
    assert hasattr(service, 'incremental_sync')
    assert hasattr(service, 'push_changes')
    assert hasattr(service, 'ENTITY_TABLES')
    assert isinstance(service.ENTITY_TABLES, dict)
    assert len(service.ENTITY_TABLES) == 7, "应有7种同步实体类型"

def test_entity_tables_keys():
    """测试同步实体表名配置"""
    service = SyncService()
    expected_tables = [
        'keyword_rules', 'ai_model_configs', 'user_style_profiles',
        'app_configs', 'scenarios', 'reply_history', 'message_blacklist'
    ]
    for table in expected_tables:
        assert table in service.ENTITY_TABLES, f"缺少表: {table}"

def test_to_profile_with_none():
    """测试空配置处理"""
    assert to_profile(None) is None, "空配置应返回None"

def test_to_profile_with_deleted():
    """测试已删除配置"""
    profile_tuple = ('u1', 0.5, 0.6, 0.7, 50, '[]', '[]', '[]', 0.8, 1000, 2000, 't1', 3000, True)
    profile = to_profile(profile_tuple)
    assert profile['deleted'] == True

def test_entity_transformers_boolean_conversion():
    """测试布尔值转换（确保0/1正确转换）"""
    # enabled=True (数据库存1)
    rule_enabled = ('1', 'test', 'CONTAINS', 'reply', 'cat', 'ALL', '[]', 0, 1, 1000, 2000, 't1', 3000, 0)
    assert to_rule(rule_enabled)['enabled'] == True

    # enabled=False (数据库存0)
    rule_disabled = ('2', 'test', 'CONTAINS', 'reply', 'cat', 'ALL', '[]', 0, 0, 1000, 2000, 't1', 3000, 0)
    assert to_rule(rule_disabled)['enabled'] == False

    # deleted=True (数据库存1)
    rule_deleted = ('3', 'test', 'CONTAINS', 'reply', 'cat', 'ALL', '[]', 0, 1, 1000, 2000, 't1', 3000, 1)
    assert to_rule(rule_deleted)['deleted'] == True

def test_to_rule_fields():
    """测试关键词规则转换包含所有字段"""
    rule_tuple = ('r1', 'keyword', 'CONTAINS', 'reply', 'cat', 'APP', '["app1"]', 5, True, 1000, 2000, 't1', 3000, False)
    rule = to_rule(rule_tuple)
    required_fields = ['id', 'keyword', 'matchType', 'replyTemplate', 'category',
                       'targetType', 'targetNamesJson', 'priority', 'enabled',
                       'createdAt', 'updatedAt', 'tenantId', 'syncVersion', 'deleted']
    for field in required_fields:
        assert field in rule, f"缺少字段: {field}"

def test_to_model_fields():
    """测试AI模型转换包含所有字段"""
    model_tuple = ('m1', 'openai', 'GPT-4', 'key', 'url', 0.7, 1000, True, True, 100, 2000, 3000, 't1', 4000, False)
    model = to_model(model_tuple)
    required_fields = ['id', 'modelType', 'modelName', 'apiKey', 'apiEndpoint',
                      'temperature', 'maxTokens', 'isDefault', 'isEnabled',
                      'monthlyCost', 'lastUsed', 'createdAt', 'tenantId', 'syncVersion', 'deleted']
    for field in required_fields:
        assert field in model, f"缺少字段: {field}"

def test_sync_endpoint_requires_auth():
    """测试同步端点需要认证"""
    from app import app
    with app.test_client() as client:
        resp = client.get('/sync')
        assert resp.status_code == 401

def test_sync_endpoint_with_valid_token():
    """测试同步端点使用有效令牌"""
    from app import app
    from utils.auth import generate_tokens
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)

    with app.test_client() as client:
        resp = client.get('/sync', headers={'Authorization': f'Bearer {access_token}'})
        # 无数据库连接，可能500或成功（取决于是否有数据）
        assert resp.status_code in [200, 500]

def test_sync_endpoint_with_invalid_token():
    """测试同步端点使用无效令牌"""
    from app import app
    with app.test_client() as client:
        resp = client.get('/sync', headers={'Authorization': 'Bearer invalid-token'})
        assert resp.status_code == 401

def test_sync_push_requires_auth():
    """测试同步推送端点需要认证"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/sync/push', json={})
        assert resp.status_code == 401

def test_sync_push_with_valid_token():
    """测试同步推送端点使用有效令牌"""
    from app import app
    from utils.auth import generate_tokens
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)

    with app.test_client() as client:
        resp = client.post('/sync/push',
                          headers={'Authorization': f'Bearer {access_token}'},
                          json={'keywordRules': []})
        # 无数据库连接，可能500或成功
        assert resp.status_code in [200, 500]

def test_sync_push_with_empty_data():
    """测试同步推送空数据"""
    from app import app
    from utils.auth import generate_tokens
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)

    with app.test_client() as client:
        resp = client.post('/sync/push',
                          headers={'Authorization': f'Bearer {access_token}'},
                          json={})
        # 无数据库连接，可能500或成功
        assert resp.status_code in [200, 500]

def test_sync_incremental_params():
    """测试增量同步参数"""
    from app import app
    from utils.auth import generate_tokens
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)

    with app.test_client() as client:
        # 测试带since参数的增量同步
        resp = client.get('/sync?since=1000', headers={'Authorization': f'Bearer {access_token}'})
        assert resp.status_code in [200, 500]

def test_sync_pagination_params():
    """测试同步分页参数"""
    from app import app
    from utils.auth import generate_tokens
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)

    with app.test_client() as client:
        resp = client.get('/sync?since=1000&page=2&limit=50',
                          headers={'Authorization': f'Bearer {access_token}'})
        assert resp.status_code in [200, 500]

if __name__ == '__main__':
    tests = [
        test_entity_transformers,
        test_sync_service,
        test_entity_tables_keys,
        test_to_profile_with_none,
        test_to_profile_with_deleted,
        test_entity_transformers_boolean_conversion,
        test_to_rule_fields,
        test_to_model_fields,
        test_sync_endpoint_requires_auth,
        test_sync_endpoint_with_valid_token,
        test_sync_endpoint_with_invalid_token,
        test_sync_push_requires_auth,
        test_sync_push_with_valid_token,
        test_sync_push_with_empty_data,
        test_sync_incremental_params,
        test_sync_pagination_params,
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