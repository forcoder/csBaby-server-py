import web
import json
from models.user import create_user, authenticate_user
from utils.auth import verify_token, generate_tokens

class Register:
    def POST(self):
        try:
            data = json.loads(web.data())
            email = data.get('email')
            password = data.get('password')
            display_name = data.get('displayName')
            if not email or not password or not display_name:
                web.ctx.status = '400 Bad Request'
                return json.dumps({'code': 400, 'message': '缺少必填字段'})
            result = create_user(email, password, display_name)
            return json.dumps({'code': 0, 'message': '注册成功', 'data': result})
        except Exception as e:
            if str(e) == 'EMAIL_EXISTS':
                web.ctx.status = '409 Conflict'
                return json.dumps({'code': 409, 'message': '该邮箱已被注册'})
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})

class Login:
    def POST(self):
        try:
            data = json.loads(web.data())
            email = data.get('email')
            password = data.get('password')
            if not email or not password:
                web.ctx.status = '400 Bad Request'
                return json.dumps({'code': 400, 'message': '缺少必填字段'})
            result = authenticate_user(email, password)
            return json.dumps({'code': 0, 'message': '登录成功', 'data': result})
        except Exception as e:
            if str(e) == 'INVALID_CREDENTIALS':
                web.ctx.status = '401 Unauthorized'
                return json.dumps({'code': 401, 'message': '邮箱或密码错误'})
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})

class Refresh:
    def POST(self):
        try:
            data = json.loads(web.data())
            refresh_token = data.get('refreshToken')
            if not refresh_token:
                web.ctx.status = '400 Bad Request'
                return json.dumps({'code': 400, 'message': '缺少refreshToken'})
            payload = verify_token(refresh_token, 'refresh')
            if not payload:
                web.ctx.status = '401 Unauthorized'
                return json.dumps({'code': 401, 'message': '刷新令牌无效或已过期'})
            user_id = payload['user_id']
            tenant_id = payload['tenant_id']
            access_token, new_refresh_token = generate_tokens(user_id, tenant_id)
            return json.dumps({
                'code': 0, 'message': '刷新成功',
                'data': {'accessToken': access_token, 'refreshToken': new_refresh_token}
            })
        except Exception as e:
            web.ctx.status = '401 Unauthorized'
            return json.dumps({'code': 401, 'message': '刷新令牌无效或已过期'})