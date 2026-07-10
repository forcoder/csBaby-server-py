"""健康检查 (Flask Blueprint)。"""
from datetime import datetime
import os
from flask import Blueprint, jsonify

health_bp = Blueprint('health', __name__)


@health_bp.route('/health', methods=['GET'])
def health_check():
    try:
        from config.database import execute_query
        execute_query("SELECT 1", fetch='one')
        db_status = 'ok'
    except Exception as e:
        db_status = f'error: {str(e)}'

    return jsonify({
        'status': 'ok' if db_status == 'ok' else 'degraded',
        'service': 'csbaby-sync-server-py',
        'version': '2.0.0',
        'ts': int(datetime.now().timestamp() * 1000),
        'pid': os.getpid(),
        'uptime': int(datetime.now().timestamp()),
        'checks': {'database': db_status}
    })
