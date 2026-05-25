import web
import json
from services.sync_service import SyncService
from utils.auth import require_auth

class Sync:
    @require_auth
    def GET(self):
        try:
            user_data = web.input(since=0, page=1, limit=100)
            tenant_id = web.ctx.tenant_id
            service = SyncService()
            if int(user_data.since) == 0:
                result = service.full_sync(tenant_id)
            else:
                result = service.incremental_sync(tenant_id, int(user_data.since),
                                                  int(user_data.page), min(int(user_data.limit), 100))
            return json.dumps({'code': 0, 'message': '成功', 'data': result})
        except Exception as e:
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})

class SyncPush:
    @require_auth
    def POST(self):
        try:
            tenant_id = web.ctx.tenant_id
            data = json.loads(web.data())
            service = SyncService()
            result = service.push_changes(tenant_id, data)
            return json.dumps({'code': 0, 'message': '成功', 'data': result})
        except Exception as e:
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})