import web
import json
from services.backup_service import BackupService
from utils.auth import require_auth

class BackupUpload:
    @require_auth
    def POST(self):
        try:
            data = json.loads(web.data())
            tenant_id = web.ctx.tenant_id
            service = BackupService()
            result = service.upload_backup(
                tenant_id, data.get('deviceName'), data.get('appVersion'),
                data.get('data'), data.get('checksum'), data.get('version', '1.0'),
                data.get('backupType', 'manual')
            )
            return json.dumps({'code': 0, 'message': '备份成功', 'data': result})
        except Exception as e:
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})

class BackupList:
    @require_auth
    def GET(self):
        try:
            tenant_id = web.ctx.tenant_id
            service = BackupService()
            result = service.get_backup_list(tenant_id)
            return json.dumps({'code': 0, 'message': '成功', 'data': result})
        except Exception as e:
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})

class BackupDownload:
    @require_auth
    def GET(self, backup_id):
        try:
            tenant_id = web.ctx.tenant_id
            service = BackupService()
            result = service.download_backup(int(backup_id), tenant_id)
            return json.dumps({'code': 0, 'message': '成功', 'data': result})
        except Exception as e:
            if 'BACKUP_NOT_FOUND' in str(e):
                web.ctx.status = '404 Not Found'
                return json.dumps({'code': 404, 'message': '备份不存在'})
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})

class BackupRestore:
    @require_auth
    def POST(self, backup_id):
        try:
            tenant_id = web.ctx.tenant_id
            service = BackupService()
            result = service.restore_backup(int(backup_id), tenant_id)
            return json.dumps({'code': 0, 'message': '恢复成功', 'data': result})
        except Exception as e:
            if 'BACKUP_NOT_FOUND' in str(e):
                web.ctx.status = '404 Not Found'
                return json.dumps({'code': 404, 'message': '备份不存在'})
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})

class BackupDelete:
    @require_auth
    def DELETE(self, backup_id):
        try:
            tenant_id = web.ctx.tenant_id
            service = BackupService()
            service.delete_backup(int(backup_id), tenant_id)
            return json.dumps({'code': 0, 'message': '备份已删除'})
        except Exception as e:
            web.ctx.status = '500 Internal Server Error'
            return json.dumps({'code': 500, 'message': str(e)})