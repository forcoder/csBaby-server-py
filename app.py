"""csBaby 同步服务 (Flask) — 主入口。

所有业务路由 (health / auth / sync / backup) 拆分到 controllers/*.py,
本文件只做 Flask app 初始化 + Blueprint 注册。
"""
import os
import logging
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 初始化数据库表结构
try:
    from config.database import init_schema
    logger.info("Initializing database schema...")
    init_schema()
    logger.info("Database schema initialized successfully")
except Exception as e:
    logger.error(f"Schema initialization failed: {e}")


# ========== 根路由 ==========

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'status': 'ok',
        'service': 'csbaby-sync-server-py',
        'version': '2.0.0',
        'ts': int(datetime.now().timestamp() * 1000)
    })


# ========== 注册 Blueprint ==========

def _register_blueprints():
    """注册所有控制器 blueprint。失败不中断启动, 记录错误即可。"""
    blueprints = [
        ('health', 'controllers.health_controller', 'health_bp'),
        ('auth', 'controllers.auth_controller', 'auth_bp'),
        ('sync', 'controllers.sync_controller', 'sync_bp'),
        ('backup', 'controllers.backup_controller', 'backup_bp'),
    ]
    for name, module_path, attr in blueprints:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            bp = getattr(mod, attr)
            app.register_blueprint(bp)
            logger.info(f"Registered blueprint: {name}")
        except Exception as e:
            logger.error(f"Failed to register blueprint {name}: {e}")


_register_blueprints()


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
