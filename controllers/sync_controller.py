"""同步路由 (Flask Blueprint) — 统一从 app.py 拆出, 便于测试和维护。

端点:
  GET  /sync/all      - 全量同步
  GET  /sync/changes  - 增量同步
  POST /sync/resolve  - 冲突解决
  GET  /sync          - 兼容旧客户端 (since=0 全量, 否则增量)
  POST /sync/push     - 推送本地变更
"""
from flask import Blueprint, request, jsonify, g
from functools import wraps
import os
import jwt

sync_bp = Blueprint('sync', __name__)


def _verify_token(token, token_type='access'):
    JWT_SECRET = os.getenv('JWT_SECRET', 'default-secret-change-me')
    JWT_ALGORITHM = 'HS256'
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get('type') != token_type:
            return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def _require_auth(f):
    """Flask 风格 require_auth: 验证 Bearer token, 设置 g.tenant_id / g.user_id。

    兼容两种 token: 旧 sync token 含 tenant_id; 新主 API token 不含 → 用 user_id 兜底。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'code': 401, 'message': '缺少认证令牌'}), 401
        token = auth_header[7:]
        payload = _verify_token(token)
        if not payload:
            return jsonify({'code': 401, 'message': '令牌无效或已过期'}), 401
        g.user_id = payload['user_id']
        g.tenant_id = payload.get('tenant_id') or payload.get('user_id', '')
        return f(*args, **kwargs)
    return decorated


@sync_bp.route('/sync/all', methods=['GET'])
@_require_auth
def sync_all():
    """全量同步 - 兼容客户端 /sync/all 端点 (v2.1 添加)"""
    try:
        from services.sync_service import SyncService
        service = SyncService()
        result = service.full_sync(g.tenant_id)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500


@sync_bp.route('/sync/changes', methods=['GET'])
@_require_auth
def sync_changes():
    """增量同步 - 兼容客户端 /sync/changes 端点 (v2.1 添加)"""
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


@sync_bp.route('/sync/resolve', methods=['POST'])
@_require_auth
def sync_resolve():
    """冲突解决 - 兼容客户端"""
    try:
        return jsonify({'code': 0, 'message': '成功', 'data': {'resolved': True}})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500


@sync_bp.route('/sync', methods=['GET'])
@_require_auth
def sync_get():
    """兼容旧客户端 (since=0 全量, 否则增量)"""
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


@sync_bp.route('/sync/push', methods=['POST'])
@_require_auth
def sync_push():
    """推送本地变更到云端"""
    try:
        data = request.get_json()
        from services.sync_service import SyncService
        service = SyncService()
        result = service.push_changes(g.tenant_id, data)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500
