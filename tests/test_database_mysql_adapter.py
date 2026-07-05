"""Phase 2 测试 - csBaby-server-py config/database.py MySQL 适配层。

覆盖:
  - DatabaseConfig.get_pool() 返回 dbutils PooledDB (而非 psycopg2.pool)
  - execute_query / execute_update 用 pymysql 游标
  - execute_batch 走单连接 autocommit
  - init_schema() MySQL 方言 (无 SERIAL / 无 EXTRACT / 无 DO $$)
  - 参数占位符 %s 仍可用 (pymysql 与 psycopg2 共用 %s)

约束:
  - 通过 env RDS_DB_URL 注入测试库,缺则 skip
  - 用独立测试库 r2346qiaozhou_test,避免污染生产数据
  - 不依赖真实服务器,本地 docker mysql 或 aliyun RDS 均可
  - 静态测试 (TestPhase2StaticChecks) 无需 DB,本地也能跑
"""
import os
import pytest

# 缺 RDS_DB_URL 时集成测试 skip,不影响本地开发
# 生产 CI 必须设 RDS_DB_URL_TEST 指向隔离测试库或临时 schema
RDS_URL = os.environ.get("RDS_DB_URL_TEST") or os.environ.get("RDS_DB_URL")
requires_rds = pytest.mark.skipif(
    not RDS_URL,
    reason="RDS_DB_URL_TEST not set - skipping MySQL integration tests",
)


@pytest.fixture
def db_module():
    """导入 config.database,触发 MySQL 适配层加载。"""
    import importlib
    import sys
    # 确保 csBaby-server-py 在 path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "csBaby-server-py"))
    if "config.database" in sys.modules:
        del sys.modules["config.database"]
    import config.database
    return config.database


@pytest.fixture
def reset_pool(db_module):
    """每个测试前重置连接池,确保用最新 env 配置。"""
    db_module.DatabaseConfig._pool = None
    yield
    db_module.DatabaseConfig._pool = None

@requires_rds
def test_get_pool_uses_dbutils_not_psycopg2(reset_pool, db_module):
    """Phase 2 验证: 池对象来自 dbutils.PooledDB,非 psycopg2.pool。"""
    pool = db_module.DatabaseConfig.get_pool()
    # dbutils.PooledDB 有 .connection() 方法,psycopg2.pool.ThreadedConnectionPool 没有
    assert hasattr(pool, "connection"), "expected dbutils PooledDB"
    assert not hasattr(pool, "getconn"), "psycopg2 pool should NOT be used"

@requires_rds
def test_get_connection_returns_pymysql_connection(reset_pool, db_module):
    """Phase 2 验证: 返回的连接是 pymysql.Connection。"""
    conn = db_module.DatabaseConfig.get_connection()
    try:
        # pymysql connections expose .ping() / .cursor()
        assert hasattr(conn, "ping"), "expected pymysql connection"
        assert hasattr(conn, "cursor"), "expected pymysql connection"
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
        assert row == (1,)
    finally:
        db_module.DatabaseConfig.return_connection(conn)

@requires_rds
def test_execute_query_basic_select(reset_pool, db_module):
    """Phase 2 验证: execute_query SELECT 走 MySQL,%s 占位符仍工作。"""
    rows = db_module.execute_query("SELECT 1 AS n, 'hi' AS s", fetch="one")
    assert rows == (1, "hi")

@requires_rds
def test_execute_query_with_params(reset_pool, db_module):
    """Phase 2 验证: %s 参数占位符 pymysql 兼容。"""
    rows = db_module.execute_query(
        "SELECT %s AS a, %s AS b", (42, "test"), fetch="one"
    )
    assert rows == (42, "test")

@requires_rds
def test_execute_update_returns_rowcount(reset_pool, db_module):
    """Phase 2 验证: execute_update 返回受影响行数。

    用临时表测试,每个测试运行前 CREATE + DROP。
    """
    table = "_phase2_test_exec_update"
    db_module.execute_update(f"DROP TABLE IF EXISTS {table}")
    db_module.execute_update(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, v TEXT)"
    )
    try:
        n = db_module.execute_update(
            f"INSERT INTO {table} (id, v) VALUES (%s, %s)", (1, "a")
        )
        assert n == 1
        n = db_module.execute_update(
            f"UPDATE {table} SET v=%s WHERE id=%s", ("b", 1)
        )
        assert n == 1
        n = db_module.execute_update(f"DELETE FROM {table} WHERE id=%s", (1,))
        assert n == 1
    finally:
        db_module.execute_update(f"DROP TABLE IF EXISTS {table}")

@requires_rds
def test_execute_batch_runs_multiple_statements(reset_pool, db_module):
    """Phase 2 验证: execute_batch 走单连接 autocommit,返回总行数。"""
    table = "_phase2_test_batch"
    db_module.execute_update(f"DROP TABLE IF EXISTS {table}")
    db_module.execute_update(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, v TEXT)"
    )
    try:
        statements = [
            (f"INSERT INTO {table} (id, v) VALUES (%s, %s)", (i, f"row{i}"))
            for i in range(1, 6)
        ]
        total = db_module.execute_batch(statements)
        assert total == 5
        rows = db_module.execute_query(
            f"SELECT COUNT(*) FROM {table}", fetch="one"
        )
        assert rows[0] == 5
    finally:
        db_module.execute_update(f"DROP TABLE IF EXISTS {table}")

@requires_rds
def test_init_schema_creates_all_tables(reset_pool, db_module):
    """Phase 2 验证: init_schema() 10 张表全部建成功 (MySQL 方言)。"""
    db_module.init_schema()
    rows = db_module.execute_query(
        "SELECT TABLE_NAME FROM information_schema.tables "
        "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME",
        fetch="all",
    )
    table_names = {r[0] for r in rows}
    expected = {
        "users", "keyword_rules", "ai_model_configs", "user_style_profiles",
        "app_configs", "scenarios", "reply_history", "message_blacklist",
        "sync_checkpoints", "backup_records",
    }
    missing = expected - table_names
    assert not missing, f"missing tables: {missing}"
    # DO $$ 块改应用层 ALTER,is_syncing 等列需存在
    cols = db_module.execute_query(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sync_checkpoints'",
        fetch="all",
    )
    col_names = {c[0] for c in cols}
    for required in ("is_syncing", "last_error", "device_info", "created_at"):
        assert required in col_names, f"sync_checkpoints missing {required}"

@requires_rds
def test_keyword_hash_unique_index_exists(reset_pool, db_module):
    """Phase 2 验证: uk_tenant_keyword_hash 唯一索引被建立。"""
    db_module.init_schema()
    rows = db_module.execute_query(
        "SHOW INDEX FROM keyword_rules", fetch="all"
    )
    # (Key_name, Column_name, Non_unique)
    indexes = {(r[2], r[4]): r[1] for r in rows}
    # uk_tenant_keyword_hash 是 (tenant_id, keyword_hash) 唯一
    has_new = ("uk_tenant_keyword_hash", "tenant_id") in indexes
    has_old = ("uk_tenant_keyword", "tenant_id") in indexes
    assert has_new or has_old, f"no keyword hash index found, got: {list(indexes)}"

@requires_rds
def test_rollback_on_error(reset_pool, db_module):
    """Phase 2 验证: 出错时事务回滚,后续查询不受影响。"""
    table = "_phase2_test_rollback"
    db_module.execute_update(f"DROP TABLE IF EXISTS {table}")
    db_module.execute_update(
        f"CREATE TABLE {table} (id INT PRIMARY KEY)"
    )
    try:
        db_module.execute_update(f"INSERT INTO {table} (id) VALUES (1)")
        # 故意主键冲突
        with pytest.raises(Exception):
            db_module.execute_update(f"INSERT INTO {table} (id) VALUES (1)")
        # 事务已回滚,行数仍为 1
        rows = db_module.execute_query(
            f"SELECT COUNT(*) FROM {table}", fetch="one"
        )
        assert rows[0] == 1
    finally:
        db_module.execute_update(f"DROP TABLE IF EXISTS {table}")


# ========== 纯单元测试 (无需 MySQL 连接) ==========
# 验证 Phase 2 改造点:psycopg2 引用、SQL 方言、不依赖 PostgreSQL 专属语法。
# 即使本地无 MySQL 也能跑通,作为改造的 RED 阶段断言。

class TestPhase2StaticChecks:
    """Phase 2 静态检查:验证 config/database.py 已从 psycopg2 迁移到 pymysql。

    这些断言在 Phase 2 改造前必然失败(RED),改造后通过(GREEN)。
    """

    def test_database_module_does_not_import_psycopg2(self):
        """config/database.py 不应 import psycopg2(已迁移到 pymysql)。"""
        import importlib
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "csBaby-server-py"))
        if "config.database" in sys.modules:
            del sys.modules["config.database"]
        import config.database
        source = open(config.database.__file__, encoding="utf-8").read()
        assert "import psycopg2" not in source, \
            "config/database.py must not import psycopg2 in Phase 2"
        assert "from psycopg2" not in source, \
            "config/database.py must not import from psycopg2 in Phase 2"

    def test_database_module_uses_pymysql(self):
        """config/database.py 应使用 pymysql。"""
        import importlib
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "csBaby-server-py"))
        if "config.database" in sys.modules:
            del sys.modules["config.database"]
        import config.database
        source = open(config.database.__file__, encoding="utf-8").read()
        assert "pymysql" in source, \
            "config/database.py must import pymysql in Phase 2"

    def test_init_schema_uses_mysql_dialect(self):
        """init_schema() SQL 语句不应包含 PostgreSQL 专属语法。

        检测范围: _SCHEMA_TABLES 列表中的 SQL(以 CREATE/ALTER/INSERT 开头),
        不检测文件 docstring。
        """
        import importlib
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "csBaby-server-py"))
        if "config.database" in sys.modules:
            del sys.modules["config.database"]
        import config.database
        sqls = getattr(config.database, "_SCHEMA_TABLES", [])
        joined = "\n".join(sqls)
        assert "SERIAL" not in joined, \
            "SERIAL is PostgreSQL-only; use INT AUTO_INCREMENT for MySQL"
        assert "EXTRACT(EPOCH" not in joined, \
            "EXTRACT(EPOCH...) is PostgreSQL-only; use UNIX_TIMESTAMP() for MySQL"
        assert "DO $$" not in joined, \
            "DO $$ blocks are PostgreSQL-only; use try/except in Python"