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

if __name__ == '__main__':
    tests = [
        test_password_hash_and_verify,
        test_generate_and_verify_token,
        test_verify_invalid_token,
        test_token_type_mismatch,
        test_password_unicode,
        test_different_passwords_different_hashes,
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