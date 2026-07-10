import jwt
import bcrypt
import os
from functools import wraps
from datetime import datetime
from flask import request, jsonify, g

JWT_SECRET = os.getenv('JWT_SECRET', 'default-secret-change-me')
JWT_ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRY = 24 * 60 * 60
REFRESH_TOKEN_EXPIRY = 30 * 24 * 60 * 60

def generate_tokens(user_id, tenant_id):
    now = int(datetime.now().timestamp())
    access_payload = {
        'user_id': user_id,
        'tenant_id': tenant_id,
        'type': 'access',
        'iat': now,
        'exp': now + ACCESS_TOKEN_EXPIRY
    }
    access_token = jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    refresh_payload = {
        'user_id': user_id,
        'tenant_id': tenant_id,
        'type': 'refresh',
        'iat': now,
        'exp': now + REFRESH_TOKEN_EXPIRY
    }
    refresh_token = jwt.encode(refresh_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return access_token, refresh_token

def verify_token(token, token_type='access'):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get('type') != token_type:
            import logging
            logging.warning(f"token type mismatch: got {payload.get('type')!r}, expected {token_type!r}")
            return None
        return payload
    except jwt.ExpiredSignatureError:
        import logging
        logging.warning("token expired")
        return None
    except jwt.InvalidTokenError as e:
        import logging
        logging.warning(f"token invalid: {e}")
        return None

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def require_auth(func):
    """Flask 风格 require_auth: 验证 Bearer token, 设置 g.tenant_id / g.user_id。

    兼容两种 token:
      - 旧 sync token 含 tenant_id 字段
      - 新主 API token 不含 tenant_id → 用 user_id 兜底
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'code': 401, 'message': '缺少认证令牌'}), 401
        token = auth_header[7:]
        payload = verify_token(token)
        if not payload:
            return jsonify({'code': 401, 'message': '令牌无效或已过期'}), 401
        g.user_id = payload['user_id']
        g.tenant_id = payload.get('tenant_id') or payload.get('user_id', '')
        return func(*args, **kwargs)
    return wrapper
