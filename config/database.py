import os
import psycopg2
from psycopg2 import pool

class DatabaseConfig:
    _pool = None

    @classmethod
    def get_pool(cls):
        if cls._pool is None:
            cls._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                database=os.getenv('DB_NAME', 'csbaby'),
                user=os.getenv('DB_USER', 'postgres'),
                password=os.getenv('DB_PASSWORD', 'postgres'),
                host=os.getenv('DB_HOST', 'localhost'),
                port=os.getenv('DB_PORT', '5432')
            )
        return cls._pool

    @classmethod
    def get_connection(cls):
        return cls.get_pool().getconn()

    @classmethod
    def return_connection(cls, conn):
        cls.get_pool().putconn(conn)

def execute_query(sql, params=None, fetch='all'):
    conn = DatabaseConfig.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        if fetch == 'one':
            result = cursor.fetchone()
        else:
            result = cursor.fetchall()
        conn.commit()
        return result
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        DatabaseConfig.return_connection(conn)

def execute_update(sql, params=None):
    conn = DatabaseConfig.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        DatabaseConfig.return_connection(conn)

def init_schema():
    """初始化数据库表结构"""
    import logging
    logger = logging.getLogger(__name__)
    tables = [
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            display_name TEXT, tenant_id TEXT NOT NULL,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT,
            updated_at BIGINT, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS keyword_rules (
            id TEXT PRIMARY KEY, keyword TEXT, match_type TEXT, reply_template TEXT, category TEXT,
            target_type TEXT, target_names_json TEXT, priority INT DEFAULT 0, enabled BOOLEAN DEFAULT TRUE,
            created_at BIGINT, updated_at BIGINT, tenant_id TEXT NOT NULL,
            sync_version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS ai_model_configs (
            id TEXT PRIMARY KEY, model_type TEXT, model_name TEXT, api_key TEXT, api_endpoint TEXT,
            temperature REAL DEFAULT 0.7, max_tokens INT DEFAULT 1000, is_default BOOLEAN DEFAULT FALSE,
            is_enabled BOOLEAN DEFAULT TRUE, monthly_cost REAL DEFAULT 0, last_used BIGINT, created_at BIGINT,
            tenant_id TEXT NOT NULL, sync_version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS user_style_profiles (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, formality_level REAL DEFAULT 0.5,
            enthusiasm_level REAL DEFAULT 0.5, professionalism_level REAL DEFAULT 0.5,
            word_count_preference INT DEFAULT 50, common_phrases TEXT DEFAULT '[]', avoid_phrases TEXT DEFAULT '[]',
            learning_samples TEXT DEFAULT '[]', accuracy_score REAL DEFAULT 0.5, last_trained BIGINT, created_at BIGINT,
            tenant_id TEXT NOT NULL, sync_version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS app_configs (
            package_name TEXT PRIMARY KEY, app_name TEXT, icon_uri TEXT, is_monitored BOOLEAN DEFAULT TRUE,
            created_at BIGINT, last_used BIGINT, tenant_id TEXT NOT NULL,
            sync_version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS scenarios (
            id TEXT PRIMARY KEY, name TEXT, type TEXT, target_id TEXT, description TEXT,
            created_at BIGINT, tenant_id TEXT NOT NULL, sync_version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS reply_history (
            id TEXT PRIMARY KEY, source_app TEXT, original_message TEXT, generated_reply TEXT, final_reply TEXT,
            rule_matched_id TEXT, model_used_id TEXT, style_applied BOOLEAN DEFAULT FALSE, send_time BIGINT,
            modified BOOLEAN DEFAULT FALSE, tenant_id TEXT NOT NULL, sync_version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS message_blacklist (
            id TEXT PRIMARY KEY, type TEXT, value TEXT, description TEXT, package_name TEXT, created_at BIGINT,
            is_enabled BOOLEAN DEFAULT TRUE, tenant_id TEXT NOT NULL, sync_version BIGINT DEFAULT 0, deleted BOOLEAN DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS sync_checkpoints (
            tenant_id TEXT PRIMARY KEY, last_sync_version BIGINT DEFAULT 0, last_sync_time BIGINT, updated_at BIGINT
        )""",
        """CREATE TABLE IF NOT EXISTS backup_records (
            id SERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, device_name TEXT, app_version TEXT, data_json TEXT,
            data_size BIGINT, checksum TEXT, version TEXT DEFAULT '1.0', backup_type TEXT DEFAULT 'manual',
            created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT, deleted BOOLEAN DEFAULT FALSE
        )""",
    ]
    conn = DatabaseConfig.get_connection()
    try:
        cursor = conn.cursor()
        for sql in tables:
            cursor.execute(sql)
            logger.info(f"Executed: {sql[:50]}...")
        conn.commit()
        logger.info("All tables created successfully")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
        conn.rollback()
        raise e
    finally:
        DatabaseConfig.return_connection(conn)