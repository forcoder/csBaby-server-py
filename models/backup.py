import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.database import execute_query, execute_update
from datetime import datetime

MAX_BACKUPS_PER_TENANT = 5

def create_backup(tenant_id, device_name, app_version, data, checksum=None, version='1.0', backup_type='manual'):
    now = int(datetime.now().timestamp() * 1000)
    data_json = json.dumps(data) if not isinstance(data, str) else data
    data_size = len(data_json.encode('utf-8'))
    existing = execute_query(
        "SELECT id FROM backup_records WHERE tenant_id=%s ORDER BY created_at ASC", (tenant_id,)
    )
    if len(existing) >= MAX_BACKUPS_PER_TENANT:
        execute_update("DELETE FROM backup_records WHERE id=%s", (existing[0][0],))
    execute_update(
        """INSERT INTO backup_records (tenant_id, device_name, app_version, data_json, data_size,
           checksum, version, backup_type, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tenant_id, device_name or '未知设备', app_version or '', data_json, data_size,
         checksum or '', version, backup_type, now)
    )
    record = execute_query(
        """SELECT id, device_name, app_version, data_size, checksum, version, backup_type, created_at
           FROM backup_records WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 1""",
        (tenant_id,), fetch='one'
    )
    return {
        'id': record[0], 'deviceName': record[1], 'appVersion': record[2],
        'dataSize': record[3], 'checksum': record[4], 'version': record[5],
        'backupType': record[6], 'createdAt': record[7]
    }

def list_backups(tenant_id):
    records = execute_query(
        """SELECT id, device_name, app_version, data_size, checksum, version, backup_type, created_at
           FROM backup_records WHERE tenant_id=%s ORDER BY created_at DESC""",
        (tenant_id,)
    )
    return [{
        'id': r[0], 'deviceName': r[1], 'appVersion': r[2],
        'dataSize': r[3], 'checksum': r[4], 'version': r[5],
        'backupType': r[6], 'createdAt': r[7]
    } for r in records]

def get_backup(backup_id, tenant_id):
    record = execute_query(
        "SELECT * FROM backup_records WHERE id=%s AND tenant_id=%s",
        (backup_id, tenant_id), fetch='one'
    )
    if not record:
        return None
    return {
        'id': record[0], 'tenant_id': record[1], 'deviceName': record[2], 'appVersion': record[3],
        'data': json.loads(record[4]), 'checksum': record[5], 'version': record[6],
        'backupType': record[7], 'createdAt': record[8]
    }

def delete_backup(backup_id, tenant_id):
    execute_update("DELETE FROM backup_records WHERE id=%s AND tenant_id=%s", (backup_id, tenant_id))