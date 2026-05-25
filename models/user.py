import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.database import execute_query, execute_update
from utils.auth import hash_password, verify_password, generate_tokens
import uuid
from datetime import datetime

def create_user(email, password, display_name):
    existing = execute_query(
        "SELECT id FROM users WHERE email = %s",
        (email,)
    )
    if existing:
        raise Exception('EMAIL_EXISTS')
    user_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    password_hash = hash_password(password)
    now = int(datetime.now().timestamp() * 1000)
    execute_update(
        """INSERT INTO users (id, email, password_hash, display_name, tenant_id, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, email, password_hash, display_name, tenant_id, now)
    )
    access_token, refresh_token = generate_tokens(user_id, tenant_id)
    return {
        'userId': user_id,
        'tenantId': tenant_id,
        'accessToken': access_token,
        'refreshToken': refresh_token
    }

def authenticate_user(email, password):
    result = execute_query(
        "SELECT id, password_hash, tenant_id FROM users WHERE email = %s",
        (email,),
        fetch='one'
    )
    if not result:
        raise Exception('INVALID_CREDENTIALS')
    user_id, password_hash, tenant_id = result
    if not verify_password(password, password_hash):
        raise Exception('INVALID_CREDENTIALS')
    access_token, refresh_token = generate_tokens(user_id, tenant_id)
    return {
        'userId': user_id,
        'tenantId': tenant_id,
        'accessToken': access_token,
        'refreshToken': refresh_token
    }