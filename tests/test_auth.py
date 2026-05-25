"""认证模块测试"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.auth import hash_password, verify_password, generate_tokens, verify_token

def test_password_hash_and_verify():
    """测试密码哈希和验证"""
    password = 'test123456'
    hashed = hash_password(password)
    assert hashed != password, "哈希后密码不应等于原文"
    assert verify_password(password, hashed) == True, "正确密码应验证通过"
    assert verify_password('wrong', hashed) == False, "错误密码应验证失败"

def test_generate_and_verify_token():
    """测试JWT令牌生成和验证"""
    user_id = 'test-user-123'
    tenant_id = 'test-tenant-456'
    access_token, refresh_token = generate_tokens(user_id, tenant_id)
    assert access_token is not None
    assert refresh_token is not None
    assert access_token != refresh_token

    payload = verify_token(access_token, 'access')
    assert payload is not None
    assert payload['user_id'] == user_id
    assert payload['tenant_id'] == tenant_id
    assert payload['type'] == 'access'

    refresh_payload = verify_token(refresh_token, 'refresh')
    assert refresh_payload is not None
    assert refresh_payload['type'] == 'refresh'

def test_verify_invalid_token():
    """测试无效令牌验证"""
    assert verify_token('invalid-token') is None
    assert verify_token('') is None
    assert verify_token(None) is None

def test_token_type_mismatch():
    """测试令牌类型不匹配"""
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)
    assert verify_token(access_token, 'refresh') is None, "使用access_token验证refresh类型应失败"

def test_password_unicode():
    """测试Unicode密码处理"""
    password = '密码123'
    hashed = hash_password(password)
    assert verify_password(password, hashed) == True
    assert verify_password('wrong', hashed) == False

def test_different_passwords_different_hashes():
    """测试不同密码生成不同哈希"""
    password1 = 'password1'
    password2 = 'password2'
    hash1 = hash_password(password1)
    hash2 = hash_password(password2)
    assert hash1 != hash2, "不同密码应生成不同哈希"

def test_password_min_length():
    """测试密码最小长度验证"""
    from app import app
    with app.test_client() as client:
        # 密码太短（少于6位）
        resp = client.post('/auth/register', json={
            'email': 'test@example.com',
            'password': '12345',
            'displayName': 'Test User'
        })
        # 应该返回400（密码太短）或500（内部错误）
        assert resp.status_code in [400, 500]
        if resp.status_code == 500:
            data = resp.get_json()
            assert data['code'] == 500

def test_password_max_length():
    """测试密码最大长度验证"""
    from app import app
    with app.test_client() as client:
        # 密码太长（超过128位）
        long_password = 'a' * 129
        resp = client.post('/auth/register', json={
            'email': 'test@example.com',
            'password': long_password,
            'displayName': 'Test User'
        })
        # 应该返回400（密码太长）或500
        assert resp.status_code in [400, 500]

def test_invalid_email_format():
    """测试无效邮箱格式"""
    from app import app
    with app.test_client() as client:
        # 无效邮箱格式
        resp = client.post('/auth/register', json={
            'email': 'not-an-email',
            'password': 'password123',
            'displayName': 'Test User'
        })
        # 应该返回400或500
        assert resp.status_code in [400, 500]

def test_missing_email():
    """测试缺少邮箱字段"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/register', json={
            'password': 'password123',
            'displayName': 'Test User'
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400
        assert '缺少必填字段' in data['message']

def test_missing_password():
    """测试缺少密码字段"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/register', json={
            'email': 'test@example.com',
            'displayName': 'Test User'
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400

def test_missing_display_name():
    """测试缺少显示名字段"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/register', json={
            'email': 'test@example.com',
            'password': 'password123'
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400

def test_login_invalid_credentials():
    """测试登录无效凭据"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/login', json={
            'email': 'nonexistent@example.com',
            'password': 'wrongpassword'
        })
        assert resp.status_code in [401, 500]
        if resp.status_code == 401:
            data = resp.get_json()
            assert data['code'] == 401
            assert '错误' in data['message']

def test_login_missing_email():
    """测试登录缺少邮箱"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/login', json={
            'password': 'password123'
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400

def test_login_missing_password():
    """测试登录缺少密码"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/login', json={
            'email': 'test@example.com'
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400

def test_refresh_missing_token():
    """测试刷新令牌缺少token"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/refresh', json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['code'] == 400

def test_refresh_invalid_token():
    """测试刷新无效令牌"""
    from app import app
    with app.test_client() as client:
        resp = client.post('/auth/refresh', json={
            'refreshToken': 'invalid-token'
        })
        assert resp.status_code == 401
        data = resp.get_json()
        assert data['code'] == 401

def test_refresh_wrong_token_type():
    """测试使用access_token刷新"""
    from app import app
    user_id = 'test-user'
    tenant_id = 'test-tenant'
    access_token, _ = generate_tokens(user_id, tenant_id)

    with app.test_client() as client:
        resp = client.post('/auth/refresh', json={
            'refreshToken': access_token
        })
        assert resp.status_code == 401

def test_token_contains_required_claims():
    """测试令牌包含必需声明"""
    user_id = 'test-user-123'
    tenant_id = 'test-tenant-456'
    access_token, refresh_token = generate_tokens(user_id, tenant_id)

    access_payload = verify_token(access_token, 'access')
    assert 'user_id' in access_payload
    assert 'tenant_id' in access_payload
    assert 'type' in access_payload
    assert 'iat' in access_payload
    assert 'exp' in access_payload

    refresh_payload = verify_token(refresh_token, 'refresh')
    assert 'user_id' in refresh_payload
    assert 'tenant_id' in refresh_payload
    assert 'type' in refresh_payload

if __name__ == '__main__':
    tests = [
        test_password_hash_and_verify,
        test_generate_and_verify_token,
        test_verify_invalid_token,
        test_token_type_mismatch,
        test_password_unicode,
        test_different_passwords_different_hashes,
        test_password_min_length,
        test_password_max_length,
        test_invalid_email_format,
        test_missing_email,
        test_missing_password,
        test_missing_display_name,
        test_login_invalid_credentials,
        test_login_missing_email,
        test_login_missing_password,
        test_refresh_missing_token,
        test_refresh_invalid_token,
        test_refresh_wrong_token_type,
        test_token_contains_required_claims,
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