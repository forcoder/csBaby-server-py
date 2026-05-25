import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.database import execute_query, execute_update
from datetime import datetime

def get_checkpoint(tenant_id):
    result = execute_query(
        "SELECT * FROM sync_checkpoints WHERE tenant_id=%s",
        (tenant_id,), fetch='one'
    )
    if not result:
        return None
    return {
        'tenant_id': result[0], 'last_sync_time': result[1],
        'is_syncing': result[2], 'last_error': result[3],
        'device_info': json.loads(result[4]) if result[4] else None,
        'created_at': result[5], 'updated_at': result[6]
    }

def update_checkpoint(tenant_id, last_sync_time, is_syncing=False, last_error=None, device_info=None):
    now = int(datetime.now().timestamp() * 1000)
    existing = get_checkpoint(tenant_id)
    if existing:
        execute_update(
            """UPDATE sync_checkpoints SET last_sync_time=%s, is_syncing=%s,
               last_error=%s, device_info=%s, updated_at=%s WHERE tenant_id=%s""",
            (last_sync_time, is_syncing, last_error,
             json.dumps(device_info) if device_info else None, now, tenant_id)
        )
    else:
        execute_update(
            """INSERT INTO sync_checkpoints
               (tenant_id, last_sync_time, is_syncing, last_error, device_info, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (tenant_id, last_sync_time, is_syncing, last_error,
             json.dumps(device_info) if device_info else None, now, now)
        )