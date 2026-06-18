"""认证路由 (Flask Blueprint)。

端点:
  POST /auth/register - 注册
  POST /auth/login    - 登录
  POST /auth/refresh  - 刷新 token
"""
from flask import Blueprint, request, jsonify
from models.user import create_user, authenticate_user
from utils.auth import verify_token, generate_tokens

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json() or {}
        email = data.get('email')
        password = data.get('password')
        display_name = data.get('displayName')
        if not email or not password or not display_name:
            return jsonify({'code': 400, 'message': '缺少必填字段'}), 400
        result = create_user(email, password, display_name)
        return jsonify({'code': 0, 'message': '注册成功', 'data': result})
    except Exception as e:
        if str(e) == 'EMAIL_EXISTS':
            return jsonify({'code': 409, 'message': '该邮箱已被注册'}), 409
        return jsonify({'code': 500, 'message': str(e)}), 500


@auth_bp.route('/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json() or {}
        email = data.get('email')
        password = data.get('password')
        if not email or not password:
            return jsonify({'code': 400, 'message': '缺少必填字段'}), 400
        result = authenticate_user(email, password)
        return jsonify({'code': 0, 'message': '登录成功', 'data': result})
    except Exception as e:
        if str(e) == 'INVALID_CREDENTIALS':
            return jsonify({'code': 401, 'message': '邮箱或密码错误'}), 401
        return jsonify({'code': 500, 'message': str(e)}), 500


@auth_bp.route('/auth/refresh', methods=['POST'])
def refresh():
    try:
        data = request.get_json() or {}
        refresh_token = data.get('refreshToken')
        if not refresh_token:
            return jsonify({'code': 400, 'message': '缺少refreshToken'}), 400
        payload = verify_token(refresh_token, 'refresh')
        if not payload:
            return jsonify({'code': 401, 'message': '刷新令牌无效或已过期'}), 401
        user_id = payload['user_id']
        tenant_id = payload['tenant_id']
        access_token, new_refresh_token = generate_tokens(user_id, tenant_id)
        return jsonify({
            'code': 0, 'message': '刷新成功',
            'data': {'accessToken': access_token, 'refreshToken': new_refresh_token}
        })
    except Exception:
        return jsonify({'code': 401, 'message': '刷新令牌无效或已过期'}), 401
