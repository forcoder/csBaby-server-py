"""csBaby-server-py 数据库适配层 - Phase 2 MySQL 后端。

Phase 2 改造点:
  - psycopg2 → pymysql + dbutils.PooledDB (与 db_mysql.py 风格一致)
  - PostgreSQL 方言 → MySQL 方言:
    * SERIAL → INT AUTO_INCREMENT
    * EXTRACT(EPOCH FROM NOW())*1000 → 应用层 int(time.time()*1000) 生成毫秒
    * DO $$ BEGIN ... EXCEPTION ... END $$ → Python try/except
  - 加 uk_tenant_keyword_hash 唯一索引 (Phase 2 必须重建)
  - 保留 execute_query / execute_update / execute_batch 公共 API,
    调用方零改动

约束:
  - 通过 DB_URL 环境变量注入 MySQL 连接串,示例:
      DB_URL=mysql://user:pass@host:3306/db?charset=utf8mb4
  - 或兼容 DB_HOST/PORT/USER/PASSWORD/NAME 五个环境变量(Phase 1 遗留)
  - 单测通过 db_module fixture 重置连接池以应用新 env
"""
import logging
import os
import time

import pymysql
from dbutils.pooled_db import PooledDB
from pymysql.cursors import Cursor

logger = logging.getLogger(__name__)


def _build_mysql_kwargs() -> dict:
    """从 env 构造 pymysql 连接参数。

    优先 DB_URL (mysql://...),否则用 DB_HOST/PORT/USER/PASSWORD/NAME 兼容旧配置。
    """
    url = os.environ.get("DB_URL")
    if url:
        if url.startswith("mysql+pymysql://"):
            url = url[len("mysql+pymysql://"):]
        elif url.startswith("mysql://"):
            url = url[len("mysql://"):]
        else:
            raise ValueError(f"DB_URL must start with mysql://, got: {url[:30]}...")
        creds, rest = url.split("@", 1)
        user, password = creds.split(":", 1)
        if "/" in rest:
            host_port, db_part = rest.split("/", 1)
        else:
            host_port, db_part = rest, ""
        if ":" in host_port:
            host, port = host_port.split(":", 1)
            port = int(port)
        else:
            host, port = host_port, 3306
        db = db_part.split("?")[0] if "?" in db_part else db_part
        return dict(host=host, port=port, user=user, password=password,
                    database=db or None, charset="utf8mb4", connect_timeout=10,
                    autocommit=False)
    # 兼容旧配置 (Phase 1 之前用 DB_HOST 等)
    return dict(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        database=os.environ.get("DB_NAME", "csbaby"),
        charset="utf8mb4",
        connect_timeout=10,
        autocommit=False,
    )


class DatabaseConfig:
    """MySQL 连接池 (dbutils PooledDB)。

    Phase 2 替代 psycopg2.pool.ThreadedConnectionPool,
    接口兼容 .get_connection() / .return_connection()。
    """
    _pool: PooledDB | None = None

    @classmethod
    def get_pool(cls) -> PooledDB:
        if cls._pool is None:
            kwargs = _build_mysql_kwargs()
            cls._pool = PooledDB(
                creator=pymysql,
                mincached=1,
                maxcached=5,
                maxconnections=10,
                blocking=True,
                **kwargs,
            )
            logger.info("MySQL pool initialized host=%s db=%s",
                        kwargs["host"], kwargs["database"])
        return cls._pool

    @classmethod
    def get_connection(cls):
        """获取一个连接 (pymysql.Connection,支持 with 上下文)。"""
        return cls.get_pool().connection()

    @classmethod
    def return_connection(cls, conn) -> None:
        """归还连接到池。dbutils 下 conn.close() 即归还。"""
        try:
            conn.close()
        except Exception:
            pass


def _now_ms() -> int:
    """毫秒时间戳,替代 PG 的 (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT。"""
    return int(time.time() * 1000)


def execute_query(sql: str, params=None, fetch: str = "all"):
    """执行 SELECT,返回 fetchall/fetchone 结果(游标元组)。

    Phase 2 沿用 Phase 1 调用语义:
      - params: 元组或 None
      - fetch='one' → 返回第一行;其他 → 全部行
      - 自动 commit,出错 rollback
    """
    conn = DatabaseConfig.get_connection()
    try:
        cursor: Cursor = conn.cursor()
        cursor.execute(sql, params or ())
        if fetch == "one":
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


def execute_update(sql: str, params=None) -> int:
    """执行 INSERT/UPDATE/DELETE,返回受影响行数。

    Phase 2 沿用 Phase 1 行为:连接池统一管理,出错 rollback。
    """
    conn = DatabaseConfig.get_connection()
    try:
        cursor: Cursor = conn.cursor()
        cursor.execute(sql, params or ())
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        DatabaseConfig.return_connection(conn)


def execute_batch(statements) -> int:
    """批量执行多条语句,共用一条连接,逐条自动提交(autocommit=True)。

    Phase 2: 自动将 PostgreSQL ON CONFLICT 语法翻译为 MySQL ON DUPLICATE KEY UPDATE。
    这样调用方 (sync_service.py 等) 无需手动改 SQL。
    """
    if not statements:
        return 0
    conn = DatabaseConfig.get_connection()
    total = 0
    try:
        cursor: Cursor = conn.cursor()
        # pymysql: 关闭 autocommit,每条显式 commit
        conn.autocommit(False)
        for sql, params in statements:
            try:
                # Phase 2 适配: PG → MySQL upsert 语法转换
                translated = _pg_to_mysql_upsert(sql)
                cursor.execute(translated, params or ())
                conn.commit()
                total += cursor.rowcount
            except Exception as inner_e:
                conn.rollback()
                logger.error(f"execute_batch 单条失败: {inner_e}")
                raise
        return total
    except Exception as e:
        logger.error(f"execute_batch 失败: {e}")
        raise
    finally:
        DatabaseConfig.return_connection(conn)


def _pg_to_mysql_upsert(sql: str) -> str:
    """Phase 2 适配: PostgreSQL ON CONFLICT 语法 → MySQL ON DUPLICATE KEY UPDATE。

    输入:
      ON CONFLICT (col) DO UPDATE SET x=EXCLUDED.x, y=EXCLUDED.y

    输出:
      ON DUPLICATE KEY UPDATE x=VALUES(x), y=VALUES(y)
    """
    import re
    pattern = re.compile(
        r"ON\s+CONFLICT\s+\([^)]+\)\s+DO\s+UPDATE\s+SET\s+([^\n]+?)(?=\n\s*(?:VALUES|\(|$|ON|;))",
        re.IGNORECASE,
    )
    def repl(m):
        set_clause = m.group(1).strip()
        set_clause = re.sub(r"(\w+)\s*=\s*EXCLUDED\.(\w+)", r"\1=VALUES(\2)", set_clause)
        return f"ON DUPLICATE KEY UPDATE {set_clause}"
    return pattern.sub(repl, sql)


# ========== Schema (MySQL 方言) ==========

# Phase 2 适配:
#   - SERIAL PRIMARY KEY → INT AUTO_INCREMENT PRIMARY KEY (除 backup_records)
#   - TEXT (无长度) → TEXT (MySQL 支持) 或 varchar(N)
#   - BOOLEAN → TINYINT(1) (隐式转换)
#   - EXTRACT(EPOCH FROM NOW())*1000 → 应用层 _now_ms()
#   - DO $$ BEGIN ... EXCEPTION ... END $$ → Python try/except 包裹 ALTER

_SCHEMA_TABLES = [
    """CREATE TABLE IF NOT EXISTS users (
        id VARCHAR(64) PRIMARY KEY,
        email VARCHAR(191) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        display_name VARCHAR(191),
        tenant_id VARCHAR(64) NOT NULL,
        created_at BIGINT NOT NULL,
        updated_at BIGINT,
        deleted TINYINT(1) DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS keyword_rules (
        id VARCHAR(64) PRIMARY KEY,
        keyword TEXT,
        match_type VARCHAR(50),
        reply_template TEXT,
        category VARCHAR(100),
        target_type VARCHAR(50),
        target_names_json TEXT,
        priority INT DEFAULT 0,
        enabled TINYINT(1) DEFAULT 1,
        created_at BIGINT,
        updated_at BIGINT,
        tenant_id VARCHAR(64) NOT NULL,
        sync_version BIGINT DEFAULT 0,
        deleted TINYINT(1) DEFAULT 0,
        keyword_hash VARCHAR(64),
        INDEX idx_keyword_tenant (tenant_id),
        INDEX idx_keyword_version (sync_version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS ai_model_configs (
        id VARCHAR(64) PRIMARY KEY,
        model_type VARCHAR(50),
        model_name VARCHAR(200),
        api_key TEXT,
        api_endpoint TEXT,
        temperature DOUBLE DEFAULT 0.7,
        max_tokens INT DEFAULT 1000,
        is_default TINYINT(1) DEFAULT 0,
        is_enabled TINYINT(1) DEFAULT 1,
        monthly_cost DOUBLE DEFAULT 0,
        last_used BIGINT,
        created_at BIGINT,
        tenant_id VARCHAR(64) NOT NULL,
        sync_version BIGINT DEFAULT 0,
        deleted TINYINT(1) DEFAULT 0,
        INDEX idx_ai_model_tenant (tenant_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS user_style_profiles (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        formality_level DOUBLE DEFAULT 0.5,
        enthusiasm_level DOUBLE DEFAULT 0.5,
        professionalism_level DOUBLE DEFAULT 0.5,
        word_count_preference INT DEFAULT 50,
        common_phrases TEXT,
        avoid_phrases TEXT,
        learning_samples TEXT,
        accuracy_score DOUBLE DEFAULT 0.5,
        last_trained BIGINT,
        created_at BIGINT,
        tenant_id VARCHAR(64) NOT NULL,
        sync_version BIGINT DEFAULT 0,
        deleted TINYINT(1) DEFAULT 0,
        INDEX idx_profile_tenant (tenant_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS app_configs (
        package_name VARCHAR(191) PRIMARY KEY,
        app_name VARCHAR(191),
        icon_uri TEXT,
        is_monitored TINYINT(1) DEFAULT 1,
        created_at BIGINT,
        last_used BIGINT,
        tenant_id VARCHAR(64) NOT NULL,
        sync_version BIGINT DEFAULT 0,
        deleted TINYINT(1) DEFAULT 0,
        INDEX idx_app_tenant (tenant_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS scenarios (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(200),
        type VARCHAR(50),
        target_id VARCHAR(64),
        description TEXT,
        created_at BIGINT,
        tenant_id VARCHAR(64) NOT NULL,
        sync_version BIGINT DEFAULT 0,
        deleted TINYINT(1) DEFAULT 0,
        INDEX idx_scenario_tenant (tenant_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS reply_history (
        id VARCHAR(64) PRIMARY KEY,
        source_app VARCHAR(255),
        original_message TEXT,
        generated_reply TEXT,
        final_reply TEXT,
        rule_matched_id VARCHAR(64),
        model_used_id VARCHAR(64),
        style_applied TINYINT(1) DEFAULT 0,
        send_time BIGINT,
        modified TINYINT(1) DEFAULT 0,
        tenant_id VARCHAR(64) NOT NULL,
        sync_version BIGINT DEFAULT 0,
        deleted TINYINT(1) DEFAULT 0,
        INDEX idx_reply_tenant (tenant_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS message_blacklist (
        id VARCHAR(64) PRIMARY KEY,
        type VARCHAR(50),
        value TEXT,
        description TEXT,
        package_name VARCHAR(255),
        created_at BIGINT,
        is_enabled TINYINT(1) DEFAULT 1,
        tenant_id VARCHAR(64) NOT NULL,
        sync_version BIGINT DEFAULT 0,
        deleted TINYINT(1) DEFAULT 0,
        INDEX idx_blacklist_tenant (tenant_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS sync_checkpoints (
        tenant_id VARCHAR(64) PRIMARY KEY,
        last_sync_version BIGINT DEFAULT 0,
        last_sync_time BIGINT,
        updated_at BIGINT,
        is_syncing TINYINT(1) DEFAULT 0,
        last_error TEXT,
        device_info TEXT,
        created_at BIGINT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS backup_records (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        device_name VARCHAR(191),
        app_version VARCHAR(50),
        data_json LONGTEXT,
        data_size BIGINT,
        checksum VARCHAR(64),
        version VARCHAR(20) DEFAULT '1.0',
        backup_type VARCHAR(20) DEFAULT 'manual',
        created_at BIGINT,
        deleted TINYINT(1) DEFAULT 0,
        INDEX idx_backup_tenant (tenant_id),
        INDEX idx_backup_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def init_schema() -> None:
    """初始化 MySQL schema,Phase 2 适配版。

    Phase 2 适配:
      - DO $$ 块改为应用层 try/except (MySQL 无 DO 块语法)
      - keyword_hash 唯一索引重建 (uk_tenant_keyword_hash)
      - created_at 默认值改为应用层生成
    """
    conn = DatabaseConfig.get_connection()
    try:
        cursor: Cursor = conn.cursor()
        for sql in _SCHEMA_TABLES:
            try:
                cursor.execute(sql)
                logger.info(f"Executed: {sql[:50]}...")
            except Exception as e:
                logger.warning(f"Table creation warning (may already exist): {e}")
                conn.rollback()

        # Application-level ALTER (替代 DO $$ BEGIN ... EXCEPTION)
        # sync_checkpoints 增量列 (历史遗留)
        for col, typedef in [
            ("is_syncing", "TINYINT(1) DEFAULT 0"),
            ("last_error", "TEXT"),
            ("device_info", "TEXT"),
            ("created_at", "BIGINT"),
        ]:
            try:
                cursor.execute(
                    f"ALTER TABLE sync_checkpoints ADD COLUMN {col} {typedef}"
                )
            except Exception:
                # 列已存在 → 忽略
                conn.rollback()
                logger.info(f"sync_checkpoints.{col} already exists")
            else:
                conn.commit()
                logger.info(f"ALTER sync_checkpoints ADD {col}")

        # keyword_rules 加 keyword_hash 列 (如有)
        try:
            cursor.execute(
                "ALTER TABLE keyword_rules ADD COLUMN keyword_hash VARCHAR(64)"
            )
        except Exception:
            conn.rollback()
        else:
            conn.commit()

        # 重建唯一索引: 旧 uk_tenant_keyword → 新 uk_tenant_keyword_hash
        try:
            cursor.execute("DROP INDEX uk_tenant_keyword ON keyword_rules")
        except Exception:
            conn.rollback()
        else:
            conn.commit()
            logger.info("Dropped old uk_tenant_keyword")
        try:
            cursor.execute(
                "CREATE UNIQUE INDEX uk_tenant_keyword_hash "
                "ON keyword_rules(tenant_id, keyword_hash)"
            )
        except Exception:
            conn.rollback()
        else:
            conn.commit()
            logger.info("Created uk_tenant_keyword_hash")

        logger.info("All tables created successfully")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
        conn.rollback()
        raise e
    finally:
        DatabaseConfig.return_connection(conn)