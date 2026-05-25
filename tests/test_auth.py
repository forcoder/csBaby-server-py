import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.auth import hash_password, verify_password, generate_tokens, verify_token

def test_password_hash_and_verify():
    password = 'test123456'
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) == True
    assert verify_password('wrong', hashed) == False

def test_generate_and_verify_token():
    user_id = 'test-user-123'
    tenant_id = 'test-tenant-456'
    access_token, refresh_token = generate_tokens(user_id, tenant_id)
    assert access_token is not None
    assert refresh_token is not None
    payload = verify_token(access_token, 'access')
    assert payload is not None
    assert payload['user_id'] == user_id
    assert payload['tenant_id'] == tenant_id
    assert payload['type'] == 'access'
    refresh_payload = verify_token(refresh_token, 'refresh')
    assert refresh_payload is not None
    assert refresh_payload['type'] == 'refresh'
    assert verify_token(access_token, 'refresh') is None

def test_verify_invalid_token():
    assert verify_token('invalid-token') is None
    assert verify_token('') is None