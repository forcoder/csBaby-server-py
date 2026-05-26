-- csBaby Sync Server Database Schema
-- PostgreSQL 16

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    tenant_id TEXT NOT NULL,
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT,
    updated_at BIGINT,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

-- Keyword Rules
CREATE TABLE IF NOT EXISTS keyword_rules (
    id TEXT PRIMARY KEY,
    keyword TEXT,
    match_type TEXT,
    reply_template TEXT,
    category TEXT,
    target_type TEXT,
    target_names_json TEXT,
    priority INT DEFAULT 0,
    enabled BOOLEAN DEFAULT TRUE,
    created_at BIGINT,
    updated_at BIGINT,
    tenant_id TEXT NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_keyword_tenant ON keyword_rules(tenant_id);
CREATE INDEX IF NOT EXISTS idx_keyword_version ON keyword_rules(sync_version);

-- AI Model Configs
CREATE TABLE IF NOT EXISTS ai_model_configs (
    id TEXT PRIMARY KEY,
    model_type TEXT,
    model_name TEXT,
    api_key TEXT,
    api_endpoint TEXT,
    temperature REAL DEFAULT 0.7,
    max_tokens INT DEFAULT 1000,
    is_default BOOLEAN DEFAULT FALSE,
    is_enabled BOOLEAN DEFAULT TRUE,
    monthly_cost REAL DEFAULT 0,
    last_used BIGINT,
    created_at BIGINT,
    tenant_id TEXT NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_ai_model_tenant ON ai_model_configs(tenant_id);

-- User Style Profiles
CREATE TABLE IF NOT EXISTS user_style_profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    formality_level REAL DEFAULT 0.5,
    enthusiasm_level REAL DEFAULT 0.5,
    professionalism_level REAL DEFAULT 0.5,
    word_count_preference INT DEFAULT 50,
    common_phrases TEXT DEFAULT '[]',
    avoid_phrases TEXT DEFAULT '[]',
    learning_samples TEXT DEFAULT '[]',
    accuracy_score REAL DEFAULT 0.5,
    last_trained BIGINT,
    created_at BIGINT,
    tenant_id TEXT NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_profile_tenant ON user_style_profiles(tenant_id);

-- App Configs
CREATE TABLE IF NOT EXISTS app_configs (
    package_name TEXT PRIMARY KEY,
    app_name TEXT,
    icon_uri TEXT,
    is_monitored BOOLEAN DEFAULT TRUE,
    created_at BIGINT,
    last_used BIGINT,
    tenant_id TEXT NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_app_tenant ON app_configs(tenant_id);

-- Scenarios
CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY,
    name TEXT,
    type TEXT,
    target_id TEXT,
    description TEXT,
    created_at BIGINT,
    tenant_id TEXT NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_scenario_tenant ON scenarios(tenant_id);

-- Reply History
CREATE TABLE IF NOT EXISTS reply_history (
    id TEXT PRIMARY KEY,
    source_app TEXT,
    original_message TEXT,
    generated_reply TEXT,
    final_reply TEXT,
    rule_matched_id TEXT,
    model_used_id TEXT,
    style_applied BOOLEAN DEFAULT FALSE,
    send_time BIGINT,
    modified BOOLEAN DEFAULT FALSE,
    tenant_id TEXT NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_reply_tenant ON reply_history(tenant_id);

-- Message Blacklist
CREATE TABLE IF NOT EXISTS message_blacklist (
    id TEXT PRIMARY KEY,
    type TEXT,
    value TEXT,
    description TEXT,
    package_name TEXT,
    created_at BIGINT,
    is_enabled BOOLEAN DEFAULT TRUE,
    tenant_id TEXT NOT NULL,
    sync_version BIGINT DEFAULT 0,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_blacklist_tenant ON message_blacklist(tenant_id);

-- Sync Checkpoints
CREATE TABLE IF NOT EXISTS sync_checkpoints (
    tenant_id TEXT PRIMARY KEY,
    last_sync_version BIGINT DEFAULT 0,
    last_sync_time BIGINT,
    updated_at BIGINT
);

-- Backup Records
CREATE TABLE IF NOT EXISTS backup_records (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    device_name TEXT,
    app_version TEXT,
    backup_data TEXT,
    checksum TEXT,
    version TEXT DEFAULT '1.0',
    backup_type TEXT DEFAULT 'manual',
    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT,
    deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_backup_tenant ON backups(tenant_id);
CREATE INDEX IF NOT EXISTS idx_backup_created ON backups(created_at);