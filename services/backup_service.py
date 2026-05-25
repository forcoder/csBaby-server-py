import sys
import os
import json
import hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.backup import create_backup, list_backups, get_backup, delete_backup

class BackupService:
    def upload_backup(self, tenant_id, device_name, app_version, data, checksum=None, version='1.0', backup_type='manual'):
        if checksum is None:
            data_str = json.dumps(data) if not isinstance(data, str) else data
            checksum = hashlib.md5(data_str.encode('utf-8')).hexdigest()
        return create_backup(tenant_id, device_name, app_version, data, checksum, version, backup_type)

    def get_backup_list(self, tenant_id):
        return list_backups(tenant_id)

    def download_backup(self, backup_id, tenant_id):
        backup = get_backup(backup_id, tenant_id)
        if not backup:
            raise Exception('BACKUP_NOT_FOUND')
        return backup

    def restore_backup(self, backup_id, tenant_id):
        backup = get_backup(backup_id, tenant_id)
        if not backup:
            raise Exception('BACKUP_NOT_FOUND')
        return backup['data']

    def delete_backup(self, backup_id, tenant_id):
        backup = get_backup(backup_id, tenant_id)
        if not backup:
            raise Exception('BACKUP_NOT_FOUND')
        delete_backup(backup_id, tenant_id)