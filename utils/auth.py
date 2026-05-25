import jwt
import bcrypt
import os
from functools import wraps
from datetime import datetime

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
            return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def require_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        import web
        auth_header = web.ctx.environ.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            web.ctx.status = '401 Unauthorized'
            return '{"code":401,"message":"缺少认证令牌"}'
        token = auth_header[7:]
        payload = verify_token(token)
        if not payload:
            web.ctx.status = '401 Unauthorized'
            return '{"code":401,"message":"令牌无效或已过期"}'
        web.ctx.user_id = payload['user_id']
        web.ctx.tenant_id = payload['tenant_id']
        return func(*args, **kwargs)
    return wrapper