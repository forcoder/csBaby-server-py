-- csBaby Sync Server Database Schema
-- Phase 2: MySQL 8.0+ 方言 (替代 PostgreSQL)
-- 与 config/database.py _SCHEMA_TABLES 保持一致,供 scripts/migrate_supabase_to_rds.py 等工具使用

SET sql_mode = 'STRICT_ALL_TABLES';

-- ===== Users table =====
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(191) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(191),
    tenant_id VARCHAR(64) NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT,
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_users_tenant ON users(tenant_id);

-- ===== Keyword Rules =====
CREATE TABLE IF NOT EXISTS keyword_rules (
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
    keyword_hash VARCHAR(64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_keyword_tenant ON keyword_rules(tenant_id);
CREATE INDEX idx_keyword_version ON keyword_rules(sync_version);
-- Phase 2 新增: 唯一索引 (tenant_id, keyword_hash) 替代旧 uk_tenant_keyword
CREATE UNIQUE INDEX uk_tenant_keyword_hash ON keyword_rules(tenant_id, keyword_hash);

-- ===== AI Model Configs =====
CREATE TABLE IF NOT EXISTS ai_model_configs (
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
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_ai_model_tenant ON ai_model_configs(tenant_id);

-- ===== User Style Profiles =====
CREATE TABLE IF NOT EXISTS user_style_profiles (
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
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_profile_tenant ON user_style_profiles(tenant_id);

-- ===== App Configs =====
CREATE TABLE IF NOT EXISTS app_configs (
    package_name VARCHAR(191) PRIMARY KEY,
    app_name VARCHAR(191),
    icon_uri TEXT,
    is_monitored TINYINT(1) DEFAULT 1,
    created_at BIGINT,
    last_used BIGINT,
    tenant_id VARCHAR(64) NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_app_tenant ON app_configs(tenant_id);

-- ===== Scenarios =====
CREATE TABLE IF NOT EXISTS scenarios (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(200),
    type VARCHAR(50),
    target_id VARCHAR(64),
    description TEXT,
    created_at BIGINT,
    tenant_id VARCHAR(64) NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_scenario_tenant ON scenarios(tenant_id);

-- ===== Reply History =====
CREATE TABLE IF NOT EXISTS reply_history (
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
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_reply_tenant ON reply_history(tenant_id);

-- ===== Message Blacklist =====
CREATE TABLE IF NOT EXISTS message_blacklist (
    id VARCHAR(64) PRIMARY KEY,
    type VARCHAR(50),
    value TEXT,
    description TEXT,
    package_name VARCHAR(255),
    created_at BIGINT,
    is_enabled TINYINT(1) DEFAULT 1,
    tenant_id VARCHAR(64) NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_blacklist_tenant ON message_blacklist(tenant_id);

-- ===== Sync Checkpoints =====
CREATE TABLE IF NOT EXISTS sync_checkpoints (
    tenant_id VARCHAR(64) PRIMARY KEY,
    last_sync_version BIGINT DEFAULT 0,
    last_sync_time BIGINT,
    updated_at BIGINT,
    is_syncing TINYINT(1) DEFAULT 0,
    last_error TEXT,
    device_info TEXT,
    created_at BIGINT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===== Backup Records =====
CREATE TABLE IF NOT EXISTS backup_records (
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
    deleted TINYINT(1) DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_backup_tenant ON backup_records(tenant_id);
CREATE INDEX idx_backup_created ON backup_records(created_at);