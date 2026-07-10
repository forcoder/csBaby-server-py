"""csBaby 同步服务 (Flask) — 主入口。

所有业务路由 (health / auth / sync / backup) 拆分到 controllers/*.py,
本文件只做 Flask app 初始化 + Blueprint 注册。
"""
import os
import logging
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 初始化数据库表结构
try:
    from config.database import init_schema
    logger.info("Initializing database schema...")
    init_schema()
    logger.info("Database schema initialized successfully")
except Exception as e:
    logger.error(f"Schema initialization failed: {e}")


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

# ========== 注册 Blueprint ==========

def _register_blueprints():
    """注册所有控制器 blueprint。失败不中断启动, 记录错误即可。"""
    blueprints = [
        ('health', 'controllers.health_controller', 'health_bp'),
        ('auth', 'controllers.auth_controller', 'auth_bp'),
        ('sync', 'controllers.sync_controller', 'sync_bp'),
        ('backup', 'controllers.backup_controller', 'backup_bp'),
    ]
    for name, module_path, attr in blueprints:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            bp = getattr(mod, attr)
            app.register_blueprint(bp)
            logger.info(f"Registered blueprint: {name}")
        except Exception as e:
            logger.error(f"Failed to register blueprint {name}: {e}")


_register_blueprints()


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
