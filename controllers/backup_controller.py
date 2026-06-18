"""备份路由 (Flask Blueprint)。

端点:
  POST   /api/v1/backup/upload                 - 上传备份
  GET    /api/v1/backup/list                   - 备份列表
  GET    /api/v1/backup/download/<int:id>     - 下载备份
  POST   /api/v1/backup/restore/<int:id>      - 恢复备份
  DELETE /api/v1/backup/<int:id>              - 删除备份
"""
from flask import Blueprint, request, jsonify, g
from services.backup_service import BackupService
from utils.auth import require_auth

backup_bp = Blueprint('backup', __name__)


@backup_bp.route('/api/v1/backup/upload', methods=['POST'])
@require_auth
def backup_upload():
    try:
        data = request.get_json() or {}
        service = BackupService()
        result = service.upload_backup(
            g.tenant_id, data.get('deviceName'), data.get('appVersion'),
            data.get('data'), data.get('checksum'), data.get('version', '1.0'),
            data.get('backupType', 'manual')
        )
        return jsonify({'code': 0, 'message': '备份成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500


@backup_bp.route('/api/v1/backup/list', methods=['GET'])
@require_auth
def backup_list():
    try:
        service = BackupService()
        result = service.get_backup_list(g.tenant_id)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500


@backup_bp.route('/api/v1/backup/download/<int:backup_id>', methods=['GET'])
@require_auth
def backup_download(backup_id):
    try:
        service = BackupService()
        result = service.download_backup(backup_id, g.tenant_id)
        return jsonify({'code': 0, 'message': '成功', 'data': result})
    except Exception as e:
        if 'BACKUP_NOT_FOUND' in str(e):
            return jsonify({'code': 404, 'message': '备份不存在'}), 404
        return jsonify({'code': 500, 'message': str(e)}), 500


@backup_bp.route('/api/v1/backup/restore/<int:backup_id>', methods=['POST'])
@require_auth
def backup_restore(backup_id):
    try:
        service = BackupService()
        result = service.restore_backup(backup_id, g.tenant_id)
        return jsonify({'code': 0, 'message': '恢复成功', 'data': result})
    except Exception as e:
        if 'BACKUP_NOT_FOUND' in str(e):
            return jsonify({'code': 404, 'message': '备份不存在'}), 404
        return jsonify({'code': 500, 'message': str(e)}), 500


@backup_bp.route('/api/v1/backup/<int:backup_id>', methods=['DELETE'])
@require_auth
def backup_delete(backup_id):
    try:
        service = BackupService()
        service.delete_backup(backup_id, g.tenant_id)
        return jsonify({'code': 0, 'message': '备份已删除'})
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500
