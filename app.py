import os
import sys
from flask import Flask, jsonify, request, g
from functools import wraps
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# 初始化数据库表结构
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from config.database import init_schema
    logger.info("Initializing database schema...")
    init_schema()
    logger.info("Database schema initialized successfully")
except Exception as e:
    logger.error(f"Schema initialization failed: {e}")

# ========== 工具函数 ==========

def generate_tokens(user_id, tenant_id):
    import jwt
    JWT_SECRET = os.getenv('JWT_SECRET', 'default-secret-change-me')
    JWT_ALGORITHM = 'HS256'
    ACCESS_TOKEN_EXPIRY = 24 * 60 * 60
    REFRESH_TOKEN_EXPIRY = 30 * 24 * 60 * 60

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
    import jwt
    JWT_SECRET = os.getenv('JWT_SECRET', 'default-secret-change-me')
    JWT_ALGORITHM = 'HS256'
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get('type') != token_type:
            return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

def hash_password(password):
    import bcrypt
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    import bcrypt
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'code': 401, 'message': '缺少认证令牌'}), 401
        token = auth_header[7:]
        payload = verify_token(token)
        if not payload:
            return jsonify({'code': 401, 'message': '令牌无效或已过期'}), 401
        g.user_id = payload['user_id']
        # 兼容两种 token: 旧 sync token 含 tenant_id, 新主 API token 不含 → 用 user_id 兜底
        g.tenant_id = payload.get('tenant_id') or payload.get('user_id', '')
        return f(*args, **kwargs)
    return decorated

# ========== 健康检查 ==========

@app.route('/health', methods=['GET'])
def health_check():
    try:
        from config.database import execute_query
        execute_query("SELECT 1", fetch='one')
        db_status = 'ok'
    except Exception as e:
        db_status = f'error: {str(e)}'

    return jsonify({
        'status': 'ok' if db_status == 'ok' else 'degraded',
        'service': 'csbaby-sync-server-py',
        'version': '2.0.0',
        'ts': int(datetime.now().timestamp() * 1000),
        'pid': os.getpid(),
        'checks': {'database': db_status}
    })

# ========== 根路由 ==========

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'status': 'ok',
        'service': 'csbaby-sync-server-py',
        'version': '2.0.0',
        'ts': int(datetime.now().timestamp() * 1000)
    })

# ========== 认证路由 ==========

@app.route('/auth/register', methods=['POST'])
def auth_register():
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        display_name = data.get('displayName')

        if not email or not password or not display_name:
            return jsonify({'code': 400, 'message': '缺少必填字段'}), 400

        from models.user import create_user
        result = create_user(email, password, display_name)
        return jsonify({'code': 0, 'message': '注册成功', 'data': result})
    except Exception as e:
        if str(e) == 'EMAIL_EXISTS':
            return jsonify({'code': 409, 'message': '该邮箱已被注册'}), 409
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/auth/login', methods=['POST'])
def auth_login():
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({'code': 400, 'message': '缺少必填字段'}), 400

        from models.user import authenticate_user
        result = authenticate_user(email, password)
        return jsonify({'code': 0, 'message': '登录成功', 'data': result})
    except Exception as e:
        if str(e) == 'INVALID_CREDENTIALS':
            return jsonify({'code': 401, 'message': '邮箱或密码错误'}), 401
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/auth/refresh', methods=['POST'])
def auth_refresh():
    try:
        data = request.get_json()
        refresh_token = data.get('refreshToken')

        if not refresh_token:
            return jsonify({'code': 400, 'message': '缺少refreshToken'}), 400

        payload = verify_token(refresh_token, 'refresh')
        if not payload:
            return jsonify({'code': 401, 'message': '刷新令牌无效或已过期'}), 401

        access_token, new_refresh_token = generate_tokens(payload['user_id'], payload['tenant_id'])
        return jsonify({
            'code': 0, 'message': '刷新成功',
            'data': {'accessToken': access_token, 'refreshToken': new_refresh_token}
        })
    except Exception as e:
        return jsonify({'code': 401, 'message': '刷新令牌无效或已过期'}), 401

# ========== 主 API 兼容路由 (nginx 反代后客户端实际访问的路径) ==========
# 客户端 AuthApiService 调 /api/auth/user/* (主 API 路径),
# nginx 把这些路径反向代理到本 Flask server, 没有这些 endpoint 会 500
# 解决: 在 Flask app.py 注册同功能的 alias, 复用上面 auth_login 等的逻辑

@app.route('/api/auth/user/register', methods=['POST'])
def auth_register_api():
    return auth_register()

@app.route('/api/auth/user/login', methods=['POST'])
def auth_login_api():
    return auth_login()

@app.route('/api/auth/user/refresh', methods=['POST'])
def auth_refresh_api():
    return auth_refresh()

# ========== 同步路由 (兼容客户端) ==========

@app.route('/sync/all', methods=['GET'])
@require_auth
def sync_all():
    """全量同步 - 兼容客户端 /sync/all 端点（v2.1 添加）"""
    try:
        from services.sync_service import SyncService
        service = SyncService()
        result = service.full_sync(g.tenant_id)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/sync/changes', methods=['GET'])
@require_auth
def sync_changes():
    """增量同步 - 兼容客户端 /sync/changes 端点（v2.1 添加）"""
    try:
        since = int(request.args.get('since', 0))
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 100)), 100)
        from services.sync_service import SyncService
        service = SyncService()
        result = service.incremental_sync(g.tenant_id, since, page, limit)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/sync/resolve', methods=['POST'])
@require_auth
def sync_resolve():
    """冲突解决 - 兼容客户端"""
    try:
        return jsonify({'code': 0, 'message': '成功', 'data': {'resolved': True}})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

# ========== 原同步路由 ==========

@app.route('/sync', methods=['GET'])
@require_auth
def sync_get():
    try:
        since = int(request.args.get('since', 0))
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 100)), 100)

        from services.sync_service import SyncService
        service = SyncService()
        if since == 0:
            result = service.full_sync(g.tenant_id)
        else:
            result = service.incremental_sync(g.tenant_id, since, page, limit)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/sync/push', methods=['POST'])
@require_auth
def sync_push():
    try:
        data = request.get_json()
        from services.sync_service import SyncService
        service = SyncService()
        result = service.push_changes(g.tenant_id, data)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

# ========== 备份路由 ==========

@app.route('/api/v1/backup/upload', methods=['POST'])
@require_auth
def backup_upload():
    try:
        data = request.get_json()
        from services.backup_service import BackupService
        service = BackupService()
        result = service.upload_backup(
            g.tenant_id, data.get('deviceName'), data.get('appVersion'),
            data.get('data'), data.get('checksum'), data.get('version', '1.0'),
            data.get('backupType', 'manual')
        )
        return jsonify({'code': 0, 'message': '备份成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/api/v1/backup/list', methods=['GET'])
@require_auth
def backup_list():
    try:
        from services.backup_service import BackupService
        service = BackupService()
        result = service.get_backup_list(g.tenant_id)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/api/v1/backup/download/<int:backup_id>', methods=['GET'])
@require_auth
def backup_download(backup_id):
    try:
        from services.backup_service import BackupService
        service = BackupService()
        result = service.download_backup(backup_id, g.tenant_id)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        if 'BACKUP_NOT_FOUND' in str(e):
            return jsonify({'code': 404, 'message': '备份不存在'}), 404
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/api/v1/backup/restore/<int:backup_id>', methods=['POST'])
@require_auth
def backup_restore(backup_id):
    try:
        from services.backup_service import BackupService
        service = BackupService()
        result = service.restore_backup(backup_id, g.tenant_id)
        return jsonify({'code': 0, 'message': '恢复成功', 'data': result})
    except Exception as e:
        if 'BACKUP_NOT_FOUND' in str(e):
            return jsonify({'code': 404, 'message': '备份不存在'}), 404
        return jsonify({'code': 500, 'message': str(e)}), 500

@app.route('/api/v1/backup/<int:backup_id>', methods=['DELETE'])
@require_auth
def backup_delete(backup_id):
    try:
        from services.backup_service import BackupService
        service = BackupService()
        service.delete_backup(backup_id, g.tenant_id)
        return jsonify({'code': 0, 'message': '备份已删除'})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)