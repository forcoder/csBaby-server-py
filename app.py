"""
csBaby Backend API - DDD Architecture
======================================
Domain Layer:    domain/entities, domain/repositories, domain/services
Application Layer: application/services
Infrastructure Layer: infrastructure/persistence
Presentation Layer: app.py (Flask routes)
"""
import hashlib
import json
import logging
import os
import secrets
import time
from functools import wraps
from threading import Lock
from typing import Optional
from flask import Flask, request, jsonify

logger = logging.getLogger(__name__)

# ========== DDD Imports ==========
from domain.services.auth_service import AuthService
from domain.services.ai_service import AIService
from domain.services.keyword_matcher import KeywordMatcher
from domain.entities.device import Device
from domain.entities.keyword_rule import KeywordRule
from domain.entities.model_config import ModelConfig
from domain.entities.reply_history import ReplyHistory
from domain.entities.feedback import Feedback
from infrastructure.persistence.database import init_db, get_connection
from infrastructure.persistence.device_repo_sqlite import SqliteDeviceRepository
from infrastructure.persistence.rule_repo_sqlite import SqliteRuleRepository
from infrastructure.persistence.model_repo_sqlite import SqliteModelRepository
from infrastructure.persistence.history_repo_sqlite import SqliteHistoryRepository
from infrastructure.persistence.feedback_repo_sqlite import SqliteFeedbackRepository
from infrastructure.persistence.metrics_repo_sqlite import SqliteMetricsRepository
from infrastructure.sync.sync_writer import SyncWriter
from domain.services.auth_service import AuthService as UserAuthService

# ========== 配置 ==========
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable must be set")
JWT_EXPIRE_DAYS = 30

# ========== Domain Service Instances ==========
auth_service = AuthService(JWT_SECRET, JWT_EXPIRE_DAYS)
ai_service = AIService()
keyword_matcher = KeywordMatcher()

# ========== AI model call (exposed for test mocking) ==========
call_ai_model = ai_service.call_model

# ========== Flask App ==========
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB max request body
app.secret_key = JWT_SECRET

# ========== Database ==========
_db_initialized = False

def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True

def dict_from_row(row):
    if row is None:
        return None
    return dict(row)

# ========== JWT 认证 ==========
def extract_user_id():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return auth_service.verify_token(auth_header[7:])

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = extract_user_id()
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401
        request.user_id = user_id
        return f(*args, **kwargs)
    return decorated

# ========== Rate Limiting ==========
_rate_limit_store = {}
_rate_limit_lock = Lock()

def _cleanup_rate_limit_store():
    """Remove expired entries to prevent memory leak."""
    now = time.time()
    keys_to_remove = []
    for key, timestamps in _rate_limit_store.items():
        active = [t for t in timestamps if now - t < 3600]  # Keep max 1 hour
        if not active:
            keys_to_remove.append(key)
        else:
            _rate_limit_store[key] = active
    for key in keys_to_remove:
        del _rate_limit_store[key]

def rate_limit(max_requests: int, window_seconds: int):
    """Decorator: limit requests per IP within a time window (thread-safe)."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # Periodic cleanup (1% of requests to avoid overhead)
            if hash(f"{request.remote_addr}{time.time()}") % 100 == 0:
                with _rate_limit_lock:
                    _cleanup_rate_limit_store()
            key = f"{request.remote_addr}:{request.endpoint}"
            now = time.time()
            with _rate_limit_lock:
                window = _rate_limit_store.get(key, [])
                # Remove expired timestamps
                window = [t for t in window if now - t < window_seconds]
                if len(window) >= max_requests:
                    return jsonify({"error": "rate limit exceeded"}), 429
                window.append(now)
                _rate_limit_store[key] = window
            return f(*args, **kwargs)
        return decorated
    return decorator

# ========== CORS ==========
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:8080").split(",") if o.strip()]

@app.after_request
def after_request(response):
    origin = request.headers.get("Origin", "")
    if origin in CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Vary"] = "Origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# ========== 健康检查 ==========
@app.route("/health", methods=["GET"])
def health_check():
    import datetime
    from infrastructure.persistence.db_supabase import health_check as supa_health
    health: dict = {
        "status": "ok",
        "service": "csBaby-api",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    db_status = "ok"
    outbox_pending = 0
    outbox_dead = 0
    supa_error = None
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        outbox_pending = conn.execute(
            "SELECT COUNT(*) AS c FROM sync_outbox"
        ).fetchone()["c"]
        outbox_dead = conn.execute(
            "SELECT COUNT(*) AS c FROM sync_outbox_dead"
        ).fetchone()["c"]
        conn.close()
    except Exception as exc:
        db_status = str(exc)
        health["status"] = "degraded"
    health["database"] = db_status
    try:
        supa_ok = supa_health()
        health["supabase"] = "up" if supa_ok else "down"
    except Exception as exc:
        supa_error = f"{type(exc).__name__}: {exc}"
        health["supabase"] = "down"
        health["supabase_error"] = supa_error
    health["sync_outbox_pending"] = outbox_pending
    health["sync_outbox_dead"] = outbox_dead
    # 警告条件 (不降级, 只标记): outbox 堆积超过 100 条 或 死信 > 0
    if outbox_pending > 100 or outbox_dead > 0:
        health["sync_warning"] = (
            f"sync_outbox 有 {outbox_pending} 条待重试"
            + (f", 死信 {outbox_dead} 条" if outbox_dead else "")
        )
    status_code = 200 if health["status"] == "ok" else 503
    return jsonify(health), status_code

# ========== 认证 API ==========
@app.route("/api/auth/user/register", methods=["POST"])
@rate_limit(max_requests=5, window_seconds=300)
def user_register():
    data = request.get_json() or {}
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip().lower() or None
    password = data.get("password", "")
    name = (data.get("name") or "").strip()
    if not password:
        return jsonify({"error": "password is required"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 chars"}), 400
    if not phone and not email:
        return jsonify({"error": "phone or email is required"}), 400
    if phone and len(phone) > 20:
        return jsonify({"error": "phone too long (max 20 chars)"}), 400
    if email and ("@" not in email or len(email) > 254):
        return jsonify({"error": "invalid email format"}), 400
    # phone column is UNIQUE NOT NULL — synthesize from email when absent
    if not phone:
        phone = f"email:{email}"

    from infrastructure.persistence.user_repo_sqlite import SqliteUserRepository
    repo = SqliteUserRepository()
    if repo.get_by_phone(phone):
        return jsonify({"error": "phone already registered"}), 409
    if email and repo.get_by_email(email):
        return jsonify({"error": "email already registered"}), 409

    user_id = str(__import__("uuid").uuid4())
    pw_hash, salt = UserAuthService.hash_password(password)
    repo.create(user_id=user_id, phone=phone, password_hash=pw_hash,
                salt=salt, name=name or email or phone, email=email)
    token = auth_service.generate_token(user_id)
    return jsonify({
        "user_id": user_id, "email": email,
        "phone": phone if "@" not in phone else None,
        "token": token, "expires_in": 30 * 86400,
    }), 201


@app.route("/api/auth/user/login", methods=["POST"])
@rate_limit(max_requests=5, window_seconds=300)
def user_login():
    """Login a user by phone OR email.

    Body accepts any of:
      {"phone": "138...", "password": "..."}      (legacy)
      {"email": "a@b.com", "password": "..."}
      {"identifier": "any-of-the-above", "password": "..."}
    """
    data = request.get_json() or {}
    identifier = (data.get("identifier") or data.get("phone") or data.get("email") or "").strip()
    password = data.get("password", "")
    if not identifier or not password:
        return jsonify({"error": "phone or email and password are required"}), 400

    from infrastructure.persistence.user_repo_sqlite import SqliteUserRepository
    repo = SqliteUserRepository()
    row = repo.get_by_identifier(identifier)
    if not row or not UserAuthService.verify_password(password, row["salt"], row["password_hash"]):
        return jsonify({"error": "Invalid phone/email or password"}), 401
    user_id = row["id"]
    token = auth_service.generate_token(user_id)
    return jsonify({
        "user_id": user_id, "phone": row.get("phone"), "email": row.get("email"),
        "token": token, "expires_in": 30 * 86400,
    })


@app.route("/api/auth/register", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
def register():
    data = request.get_json() or {}
    platform = data.get("platform", "android")
    if platform not in ("android", "ios", "web"):
        return jsonify({"error": "invalid platform: must be android, ios, or web"}), 400
    app_version = data.get("app_version", "")
    if not isinstance(app_version, str):
        return jsonify({"error": "app_version must be a string"}), 400
    if len(app_version) > 50:
        return jsonify({"error": "app_version too long (max 50 chars)"}), 400
    # Optional: bind to user if user_token provided
    user_token = data.get("user_token", "")
    user_id = auth_service.verify_token(user_token) if user_token else None
    device = Device.create(
        name=data.get("name", ""),
        platform=platform,
        app_version=app_version,
    )
    if user_id:
        device.token = auth_service.generate_token(user_id)
    else:
        device.token = auth_service.generate_token(device.id)
    repo = SqliteDeviceRepository()
    repo.create(device)
    # Link device to user if authenticated
    if user_id:
        db = get_connection()
        try:
            db.execute(
                "INSERT OR IGNORE INTO user_devices (user_id, device_id, platform, device_name) VALUES (?, ?, ?, ?)",
                (user_id, device.id, platform, data.get("name", "")),
            )
            db.commit()
            sync_payload = {
                "user_id": user_id,
                "device_id": device.id,
                "platform": platform,
                "device_name": data.get("name", ""),
            }
            SyncWriter(db).push("user_devices", "INSERT", None, sync_payload)
        finally:
            db.close()
    resp = {
        "device_id": device.id,
        "user_id": user_id,
        "token": device.token,
        "expires_in": 30 * 86400,
    }
    return jsonify(resp)

@app.route("/api/auth/heartbeat", methods=["POST"])
@rate_limit(max_requests=60, window_seconds=60)
@require_auth
def heartbeat():
    repo = SqliteDeviceRepository()
    repo.update_heartbeat(request.user_id)
    return jsonify({"status": "ok"})

# ========== 知识库规则 API ==========
@app.route("/api/rules", methods=["GET"])
@require_auth
def get_rules():
    repo = SqliteRuleRepository()
    rules = repo.get_by_device(request.user_id)
    return jsonify([_rule_to_dict(r) for r in rules])

@app.route("/api/rules", methods=["POST"])
@rate_limit(max_requests=30, window_seconds=60)
@require_auth
def create_rule():
    data = request.get_json() or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400
    if len(keyword) > 500:
        return jsonify({"error": "keyword too long (max 500 chars)"}), 400
    match_type = data.get("match_type", "CONTAINS")
    if match_type not in ("CONTAINS", "EXACT", "STARTS_WITH", "ENDS_WITH", "REGEX"):
        return jsonify({"error": "invalid match_type"}), 400
    priority = data.get("priority", 0)
    if not isinstance(priority, int) or priority < 0 or priority > 100:
        return jsonify({"error": "priority must be 0-100"}), 400
    target_names = data.get("target_names", [])
    if not isinstance(target_names, list):
        return jsonify({"error": "target_names must be a list"}), 400
    rule = KeywordRule(
        user_id=request.user_id,
        keyword=keyword,
        match_type=match_type,
        reply_template=data.get("reply_template", ""),
        category=data.get("category", ""),
        target_type=data.get("target_type", "ALL"),
        target_names=target_names,
        priority=priority,
    )
    repo = SqliteRuleRepository()
    created = repo.create(rule)
    return jsonify(_rule_to_dict(created))

@app.route("/api/rules/<int:rule_id>", methods=["GET"])
@require_auth
def get_rule(rule_id):
    repo = SqliteRuleRepository()
    rule = repo.get_by_id(rule_id, request.user_id)
    if not rule:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify(_rule_to_dict(rule))

@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
@rate_limit(max_requests=20, window_seconds=60)
@require_auth
def update_rule(rule_id):
    repo = SqliteRuleRepository()
    existing = repo.get_by_id(rule_id, request.user_id)
    if not existing:
        return jsonify({"error": "Rule not found"}), 404
    data = request.get_json() or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400
    if len(keyword) > 500:
        return jsonify({"error": "keyword too long (max 500 chars)"}), 400
    match_type = data.get("match_type", "CONTAINS")
    if match_type not in ("CONTAINS", "EXACT", "STARTS_WITH", "ENDS_WITH", "REGEX"):
        return jsonify({"error": "invalid match_type"}), 400
    priority = data.get("priority", 0)
    if not isinstance(priority, int) or priority < 0 or priority > 100:
        return jsonify({"error": "priority must be 0-100"}), 400
    target_names = data.get("target_names", [])
    if not isinstance(target_names, list):
        return jsonify({"error": "target_names must be a list"}), 400
    rule = KeywordRule(
        id=rule_id, user_id=request.user_id,
        keyword=keyword, match_type=match_type,
        reply_template=data.get("reply_template", ""), category=data.get("category", ""),
        target_type=data.get("target_type", "ALL"), target_names=target_names,
        priority=priority, enabled=data.get("enabled", True),
    )
    updated = repo.update(rule)
    return jsonify(_rule_to_dict(updated))

@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
@rate_limit(max_requests=20, window_seconds=60)
@require_auth
def delete_rule(rule_id):
    repo = SqliteRuleRepository()
    deleted = repo.delete(rule_id, request.user_id)
    if not deleted:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"status": "deleted", "id": rule_id})

@app.route("/api/rules/batch", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_auth
def batch_import_rules():
    data = request.get_json() or {}
    rules_data = data.get("rules", [])
    if len(rules_data) > 1000:
        return jsonify({"error": "too many rules (max 1000)"}), 400
    mode = data.get("mode", "append")
    rules = [KeywordRule(
        user_id=request.user_id, keyword=r.get("keyword", ""),
        match_type=r.get("match_type", "CONTAINS"), reply_template=r.get("reply_template", ""),
        category=r.get("category", ""), target_type=r.get("target_type", "ALL"),
        target_names=r.get("target_names", []), priority=r.get("priority", 0),
    ) for r in rules_data]
    repo = SqliteRuleRepository()
    count = repo.batch_create(rules, request.user_id, mode)
    total = len(repo.get_by_device(request.user_id))
    return jsonify({"status": "ok", "imported": count, "total": total})

def _rule_to_dict(rule: KeywordRule) -> dict:
    return {
        "id": rule.id, "userId": rule.user_id, "keyword": rule.keyword,
        "matchType": rule.match_type, "replyTemplate": rule.reply_template,
        "category": rule.category, "targetType": rule.target_type,
        "targetNames": json.dumps(rule.target_names), "priority": rule.priority,
        "enabled": int(rule.enabled),
    }

# ========== 模型配置 API ==========
@app.route("/api/models", methods=["GET"])
@require_auth
def get_models():
    repo = SqliteModelRepository()
    models = repo.get_by_device(request.user_id)
    return jsonify([_model_to_dict(m) for m in models])

@app.route("/api/models", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
@require_auth
def create_model():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    temperature = data.get("temperature", 0.7)
    if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
        return jsonify({"error": "temperature must be 0-2"}), 400
    max_tokens = data.get("max_tokens", 2000)
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 32000:
        return jsonify({"error": "max_tokens must be 1-32000"}), 400
    config = ModelConfig(
        user_id=request.user_id, name=name,
        model_type=data.get("model_type", "OPENAI"), model=data.get("model", "gpt-4o"),
        api_key=data.get("api_key", ""), api_endpoint=data.get("api_endpoint", ""),
        temperature=temperature, max_tokens=max_tokens,
        is_default=data.get("is_default", False), enabled=data.get("enabled", True),
    )
    repo = SqliteModelRepository()
    created = repo.create(config)
    return jsonify(_model_to_dict(created))

@app.route("/api/models/<int:model_id>", methods=["GET"])
@require_auth
def get_model(model_id):
    repo = SqliteModelRepository()
    config = repo.get_by_id(model_id, request.user_id)
    if not config:
        return jsonify({"error": "Model not found"}), 404
    return jsonify(_model_to_dict(config))

@app.route("/api/models/<int:model_id>", methods=["PUT"])
@rate_limit(max_requests=20, window_seconds=60)
@require_auth
def update_model(model_id):
    repo = SqliteModelRepository()
    existing = repo.get_by_id(model_id, request.user_id)
    if not existing:
        return jsonify({"error": "Model not found"}), 404
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    temperature = data.get("temperature", 0.7)
    if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
        return jsonify({"error": "temperature must be 0-2"}), 400
    max_tokens = data.get("max_tokens", 2000)
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 32000:
        return jsonify({"error": "max_tokens must be 1-32000"}), 400
    config = ModelConfig(
        id=model_id, user_id=request.user_id,
        name=name, model_type=data.get("model_type", "OPENAI"),
        model=data.get("model", ""), api_key=data.get("api_key", ""),
        api_endpoint=data.get("api_endpoint", ""), temperature=temperature,
        max_tokens=max_tokens, is_default=data.get("is_default", False),
        enabled=data.get("enabled", True),
    )
    updated = repo.update(config)
    return jsonify(_model_to_dict(updated))

@app.route("/api/models/<int:model_id>", methods=["DELETE"])
@rate_limit(max_requests=20, window_seconds=60)
@require_auth
def delete_model(model_id):
    repo = SqliteModelRepository()
    deleted = repo.delete(model_id, request.user_id)
    if not deleted:
        return jsonify({"error": "Model not found"}), 404
    return jsonify({"status": "deleted", "id": model_id})

@app.route("/api/models/<int:model_id>/test", methods=["POST"])
@require_auth
def test_model(model_id):
    repo = SqliteModelRepository()
    config = repo.get_by_id(model_id, request.user_id)
    if not config:
        return jsonify({"error": "Model not found"}), 404
    return jsonify({"success": True, "model": config.model, "tokens": 0})

def _model_to_dict(m: ModelConfig) -> dict:
    api_key = m.api_key
    if api_key:
        if len(api_key) <= 8:
            api_key = "****"
        else:
            api_key = "*" * (len(api_key) - 4) + api_key[-4:]
    return {
        "id": m.id, "userId": m.user_id, "name": m.name,
        "modelType": m.model_type, "model": m.model, "apiKey": api_key,
        "apiEndpoint": m.api_endpoint, "temperature": m.temperature,
        "maxTokens": m.max_tokens, "isDefault": int(m.is_default),
        "enabled": int(m.enabled),
    }

def _model_to_ai_config(m: ModelConfig) -> dict:
    return {
        "model_type": m.model_type, "model": m.model,
        "api_key": m.api_key, "api_endpoint": m.api_endpoint,
    }

# ========== AI 生成 API ==========
@app.route("/api/ai/generate", methods=["POST"])
@rate_limit(max_requests=30, window_seconds=60)
@require_auth
def generate_reply():
    data = request.get_json() or {}
    message = data.get("message", "")
    if not message or len(message) > 10000:
        return jsonify({"error": "message is required and must be <= 10000 chars"}), 400
    context = data.get("context") or {}
    if not isinstance(context, dict):
        context = {}
    style = data.get("style") or {}
    if not isinstance(style, dict):
        style = {}

    # 1. Try keyword matching first using domain service
    rule_repo = SqliteRuleRepository()
    rules = rule_repo.get_by_device(request.user_id)
    rule_dicts = [_rule_to_dict(r) for r in rules]
    matched = keyword_matcher.match(rule_dicts, message)
    if matched:
        template = matched[0]["reply_template"]
        reply = keyword_matcher.apply_template(template, context)
        history = ReplyHistory(
            user_id=request.user_id, original_message=message, reply_content=reply,
            source="keyword", platform=context.get("platform", ""),
            customer_name=context.get("customer_name", ""),
            house_name=context.get("house_name", ""),
        )
        SqliteHistoryRepository().create(history)
        return jsonify({
            "reply": reply, "source": "keyword", "rule_id": matched[0]["id"],
            "confidence": 1.0, "response_time_ms": 0,
        })

    # 2. Fall back to AI model
    model_repo = SqliteModelRepository()
    models = model_repo.get_by_device(request.user_id)
    enabled_models = [m for m in models if m.enabled]
    if not enabled_models:
        return jsonify({"error": "No enabled model configured"}), 400

    model_config = enabled_models[0]
    ai_config = _model_to_ai_config(model_config)

    system_prompt = "你是一个专业的客服助手，请根据用户消息生成合适的回复。"
    if style:
        formality = style.get("formality", 0.5)
        enthusiasm = style.get("enthusiasm", 0.5)
        if formality > 0.7:
            system_prompt += "请使用正式、专业的语气。"
        elif formality < 0.3:
            system_prompt += "请使用轻松、亲切的语气。"
        if enthusiasm > 0.7:
            system_prompt += "回复要有热情。"

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": message}]
    try:
        result = call_ai_model(ai_config, messages, model_config.temperature, model_config.max_tokens)
    except Exception as e:
        logger.error("AI generation failed for user %s: %s", request.user_id, e, exc_info=True)
        return jsonify({"error": "AI generation failed"}), 500

    history = ReplyHistory(
        user_id=request.user_id, original_message=message,
        reply_content=result["reply"], source="ai",
        model_used=result.get("model_used", ""), confidence=0.8,
        response_time_ms=result.get("response_time_ms", 0),
        platform=context.get("platform", ""),
        customer_name=context.get("customer_name", ""),
        house_name=context.get("house_name", ""),
    )
    SqliteHistoryRepository().create(history)

    return jsonify({
        "reply": result["reply"], "source": "ai",
        "model_used": result.get("model_used", ""),
        "confidence": 0.8, "response_time_ms": result.get("response_time_ms", 0),
        "tokens_used": result.get("tokens_used", 0),
    })

@app.route("/api/ai/chat", methods=["POST"])
@rate_limit(max_requests=30, window_seconds=60)
@require_auth
def chat():
    data = request.get_json() or {}
    messages = data.get("messages", [])
    if not messages or not isinstance(messages, list):
        return jsonify({"error": "messages is required and must be a non-empty list"}), 400
    # Validate each message has required fields
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            return jsonify({"error": f"messages[{i}] must have 'role' and 'content' fields"}), 400

    model_repo = SqliteModelRepository()
    models = model_repo.get_by_device(request.user_id)
    enabled_models = [m for m in models if m.enabled]
    if not enabled_models:
        return jsonify({"error": "No enabled model configured"}), 400

    model_config = enabled_models[0]
    ai_config = _model_to_ai_config(model_config)

    try:
        result = call_ai_model(ai_config, messages, model_config.temperature, model_config.max_tokens)
    except Exception as e:
        logger.error("AI chat failed for user %s: %s", request.user_id, e, exc_info=True)
        return jsonify({"error": "AI chat failed"}), 500

    return jsonify({
        "reply": result["reply"], "model_used": result.get("model_used", ""),
        "tokens_used": result.get("tokens_used", 0),
        "response_time_ms": result.get("response_time_ms", 0),
    })

# ========== 历史记录 API ==========
@app.route("/api/history", methods=["GET"])
@require_auth
def get_history():
    limit = min(request.args.get("limit", 50, type=int), 200)
    offset = request.args.get("offset", 0, type=int)
    repo = SqliteHistoryRepository()
    items, total = repo.get_by_device(request.user_id, limit, offset)
    return jsonify({
        "items": [{"id": i.id, "user_id": i.user_id,
                   "original_message": i.original_message, "reply_content": i.reply_content}
                  for i in items],
        "total": total, "limit": limit, "offset": offset,
    })

@app.route("/api/history", methods=["POST"])
@rate_limit(max_requests=60, window_seconds=60)
@require_auth
def record_history():
    data = request.get_json() or {}
    original_message = data.get("original_message", "")
    reply_content = data.get("reply_content", "")
    if len(original_message) > 50000:
        return jsonify({"error": "original_message too long (max 50000 chars)"}), 400
    if len(reply_content) > 50000:
        return jsonify({"error": "reply_content too long (max 50000 chars)"}), 400
    entry = ReplyHistory(
        user_id=request.user_id,
        original_message=original_message,
        reply_content=reply_content,
        source=data.get("source", "ai"), model_used=data.get("model_used", ""),
        confidence=data.get("confidence", 0), response_time_ms=data.get("response_time_ms", 0),
        platform=data.get("platform", ""), customer_name=data.get("customer_name", ""),
        house_name=data.get("house_name", ""),
    )
    repo = SqliteHistoryRepository()
    created = repo.create(entry)
    return jsonify({
        "id": created.id, "user_id": created.user_id,
        "original_message": created.original_message,
        "reply_content": created.reply_content,
        "source": created.source, "model_used": created.model_used,
        "confidence": created.confidence,
        "response_time_ms": created.response_time_ms,
        "platform": created.platform,
        "customer_name": created.customer_name,
        "house_name": created.house_name,
    })

# ========== 反馈 API ==========
@app.route("/api/feedback", methods=["GET"])
@require_auth
def get_feedback():
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    repo = SqliteFeedbackRepository()
    items = repo.get_by_device(request.user_id, limit, offset)
    return jsonify([{"id": f.id, "user_id": f.user_id, "action": f.action} for f in items])

@app.route("/api/feedback", methods=["POST"])
@rate_limit(max_requests=60, window_seconds=60)
@require_auth
def submit_feedback():
    data = request.get_json() or {}
    action = data.get("action", "")
    allowed_actions = {"generated", "accepted", "modified", "rejected"}
    if action not in allowed_actions:
        return jsonify({"error": f"invalid action: {action}"}), 400

    SqliteMetricsRepository().increment_metric(request.user_id, action)

    comment = data.get("comment", "")
    if len(comment) > 2000:
        return jsonify({"error": "comment too long (max 2000 chars)"}), 400
    fb = Feedback(
        user_id=request.user_id,
        reply_history_id=data.get("reply_history_id"),
        action=action, modified_text=data.get("modified_text", ""),
        rating=data.get("rating", 0), comment=comment,
    )
    repo = SqliteFeedbackRepository()
    created = repo.create(fb)
    return jsonify({
        "id": created.id, "user_id": created.user_id,
        "action": created.action, "modified_text": created.modified_text,
        "rating": created.rating, "comment": created.comment,
    })

# ========== 优化 API ==========
@app.route("/api/optimize/metrics", methods=["GET"])
@require_auth
def get_optimize_metrics():
    days = request.args.get("days", 7, type=int)
    repo = SqliteMetricsRepository()
    items = repo.get_by_device_and_date_range(request.user_id, days)
    return jsonify([{
        "id": m.id, "user_id": m.user_id, "date": m.date,
        "total_generated": m.total_generated, "total_accepted": m.total_accepted,
        "total_modified": m.total_modified, "total_rejected": m.total_rejected,
    } for m in items])

@app.route("/api/optimize/analyze", methods=["POST"])
@require_auth
def analyze_optimize():
    repo = SqliteMetricsRepository()
    items = repo.get_by_device_and_date_range(request.user_id, 30)
    if not items:
        return jsonify({"status": "no_data", "message": "暂无足够数据进行分析"})

    total = sum(m.total_generated for m in items)
    accepted = sum(m.total_accepted for m in items)
    modified = sum(m.total_modified for m in items)
    rejected = sum(m.total_rejected for m in items)

    accept_rate = round(accepted / total, 3) if total > 0 else 0
    modify_rate = round(modified / total, 3) if total > 0 else 0
    reject_rate = round(rejected / total, 3) if total > 0 else 0

    suggestions = []
    if accept_rate < 0.5:
        suggestions.append("接受率较低，建议优化回复模板或调整模型参数")
    if modify_rate > 0.3:
        suggestions.append("修改率较高，建议检查模板变量是否正确替换")
    if reject_rate > 0.2:
        suggestions.append("拒绝率较高，建议增加知识库规则覆盖更多场景")

    return jsonify({
        "status": "ok", "period_days": 30, "total_generated": total,
        "total_accepted": accepted, "total_modified": modified, "total_rejected": rejected,
        "accept_rate": accept_rate, "modify_rate": modify_rate, "reject_rate": reject_rate,
        "suggestions": suggestions,
    })

# ========== 备份 API ==========
@app.route("/api/backup", methods=["GET"])
@rate_limit(max_requests=10, window_seconds=60)
@require_auth
def export_backup():
    user_id = request.user_id
    rule_repo = SqliteRuleRepository()
    model_repo = SqliteModelRepository()
    history_repo = SqliteHistoryRepository()
    feedback_repo = SqliteFeedbackRepository()
    metrics_repo = SqliteMetricsRepository()

    rules = rule_repo.get_by_device(user_id)
    models = model_repo.get_by_device(user_id)
    history_items, _ = history_repo.get_by_device(user_id, 1000, 0)
    feedback_items = feedback_repo.get_by_device(user_id, 1000, 0)
    metrics = metrics_repo.get_by_device_and_date_range(user_id, 365)

    blacklist_items = []
    try:
        db = get_connection()
        bl_rows = db.execute("SELECT * FROM blacklist WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        blacklist_items = [dict(r) for r in bl_rows]
        db.close()
    except Exception:
        pass

    return jsonify({
        "version": 2, "user_id": user_id,
        "rules": [_rule_to_dict(r) for r in rules],
        "models": [_model_to_dict(m) for m in models],
        "history": [{"id": h.id, "original_message": h.original_message,
                      "reply_content": h.reply_content} for h in history_items],
        "feedback": [{"id": f.id, "action": f.action} for f in feedback_items],
        "metrics": [{"date": m.date, "total_generated": m.total_generated} for m in metrics],
        "blacklist": blacklist_items,
    })

@app.route("/api/backup/restore", methods=["POST"])
@rate_limit(max_requests=5, window_seconds=60)
@require_auth
def restore_backup():
    data = request.get_json() or {}
    backup = data.get("backup")
    if not isinstance(backup, dict):
        return jsonify({"error": "backup must be a JSON object"}), 400
    user_id = request.user_id

    rules_data = backup.get("rules", [])
    if rules_data and not isinstance(rules_data, list):
        return jsonify({"error": "rules must be a list"}), 400

    models_data = backup.get("models", [])
    if models_data and not isinstance(models_data, list):
        return jsonify({"error": "models must be a list"}), 400

    # Parse all data first before touching the database
    parsed_rules = []
    for r in rules_data:
        try:
            target_names = r.get("target_names", [])
            if isinstance(target_names, str):
                target_names = json.loads(target_names)
            if not isinstance(target_names, list):
                target_names = []
            parsed_rules.append(KeywordRule(
                user_id=user_id, keyword=r.get("keyword", ""),
                match_type=r.get("match_type", "CONTAINS"), reply_template=r.get("reply_template", ""),
                category=r.get("category", ""), target_type=r.get("target_type", "ALL"),
                target_names=target_names,
                priority=r.get("priority", 0), enabled=bool(r.get("enabled", True)),
            ))
        except (json.JSONDecodeError, TypeError) as e:
            return jsonify({"error": f"Invalid rule data: {e}"}), 400

    parsed_models = []
    for m_data in models_data:
        try:
            parsed_models.append(ModelConfig(
                user_id=user_id, name=m_data.get("name", ""),
                model_type=m_data.get("model_type", "OPENAI"), model=m_data.get("model", ""),
                api_key=m_data.get("api_key", ""), api_endpoint=m_data.get("api_endpoint", ""),
                temperature=m_data.get("temperature", 0.7), max_tokens=m_data.get("max_tokens", 2000),
                is_default=bool(m_data.get("is_default", False)), enabled=bool(m_data.get("enabled", True)),
            ))
        except (TypeError, ValueError) as e:
            return jsonify({"error": f"Invalid model data: {e}"}), 400

    # Restore rules
    if parsed_rules:
        SqliteRuleRepository().batch_create(parsed_rules, user_id, "override")

    # Restore models
    if parsed_models:
        db = get_connection()
        try:
            db.execute("DELETE FROM model_configs WHERE user_id=?", (user_id,))
            db.commit()
        finally:
            db.close()
        for config in parsed_models:
            SqliteModelRepository().create(config)

    # Restore blacklist
    bl_data = backup.get("blacklist", [])
    bl_count = 0
    if bl_data and isinstance(bl_data, list):
        db = get_connection()
        try:
            db.execute("DELETE FROM blacklist WHERE user_id=?", (user_id,))
            db.commit()
            for bl in bl_data:
                db.execute(
                    "INSERT INTO blacklist (user_id, type, value, description, package_name, is_enabled) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, bl.get("type", "KEYWORD"), bl.get("value", ""),
                     bl.get("description", ""), bl.get("package_name"),
                     1 if bl.get("is_enabled", True) else 0)
                )
            db.commit()
            bl_count = len(bl_data)
        except Exception:
            pass
        finally:
            db.close()

    return jsonify({
        "status": "ok",
        "restored": {"rules": len(parsed_rules), "models": len(parsed_models), "blacklist": bl_count},
    })

# ========== Admin Authentication ==========
# Admin accounts persisted in SQLite
_admin_table_initialized = False


def _ensure_admin_tables():
    """Ensure all admin-related tables exist (idempotent, uses CREATE IF NOT EXISTS)."""
    db = get_connection()
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS admin_accounts (
                phone TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                phone TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_admin_sessions_phone ON admin_sessions(phone);
            CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);
        """)
        db.commit()
    finally:
        db.close()


def _ensure_admin_table():
    global _admin_table_initialized
    if not _admin_table_initialized:
        _ensure_admin_tables()
        _admin_table_initialized = True

def _init_admin():
    """Create default admins if they don't exist in DB."""
    _ensure_admin_table()
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        # 仅在没有任何活跃管理员账户时，使用默认密码创建（首次部署安全兜底）
        db_check = get_connection()
        try:
            count = db_check.execute("SELECT COUNT(*) FROM admin_accounts WHERE is_active=1").fetchone()[0]
        finally:
            db_check.close()
        if count > 0:
            logger.warning("ADMIN_PASSWORD not set, but active admin accounts exist — skipping default creation")
            return
        password = "admin123"
        logger.warning("ADMIN_PASSWORD not set — using default password '%s'. Change it after first login!", password)
    db = get_connection()
    try:
        # Support multiple admin phones (comma-separated env var + hardcoded fallbacks)
        env_phones = [p.strip() for p in os.environ.get("ADMIN_PHONE", "").split(",") if p.strip()]
        default_phones = ["13800138000", "15558181817"]
        seen = set()
        for phone in env_phones + default_phones:
            if phone in seen:
                continue
            seen.add(phone)
            existing = db.execute("SELECT COUNT(*) FROM admin_accounts WHERE phone=?", (phone,)).fetchone()
            if existing[0] == 0:
                db.execute(
                    "INSERT INTO admin_accounts (phone, password_hash, is_active) VALUES (?, ?, 1)",
                    (phone, _hash_password(password))
                )
                logger.info("Created admin account: %s", phone)
        db.commit()
    finally:
        db.close()

def _hash_password(pw: str) -> str:
    import hashlib
    return hashlib.sha256(f"{pw}{JWT_SECRET}".encode()).hexdigest()

def _now_str():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _verify_admin(phone, password):
    _ensure_admin_table()
    db = get_connection()
    try:
        row = db.execute(
            "SELECT password_hash, is_active FROM admin_accounts WHERE phone=?", (phone,)
        ).fetchone()
        if not row:
            return False
        return row["password_hash"] == _hash_password(password) and bool(row["is_active"])
    finally:
        db.close()

def _get_all_admins():
    _ensure_admin_table()
    db = get_connection()
    try:
        rows = db.execute("SELECT phone, is_active, created_at FROM admin_accounts ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()

_ADMIN_SESSION_EXPIRY_HOURS = 24


def _admin_exists_in_db(phone: str) -> bool:
    """Check if an admin account exists in the SQLite database."""
    db = get_connection()
    try:
        row = db.execute("SELECT phone FROM admin_accounts WHERE phone=? AND is_active=1", (phone,)).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        db.close()


def _create_admin_session(phone: str) -> str:
    """Create a persistent admin session token with expiry."""
    _ensure_admin_tables()
    import datetime
    token = secrets.token_hex(32)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=_ADMIN_SESSION_EXPIRY_HOURS)
    db = get_connection()
    try:
        db.execute(
            "INSERT INTO admin_sessions (token, phone, expires_at) VALUES (?, ?, ?)",
            (token, phone, expires_at.strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.commit()
    finally:
        db.close()
    return token


def _validate_admin_session(token: str) -> Optional[str]:
    """Validate admin session token. Returns phone if valid, None otherwise."""
    _ensure_admin_tables()
    import datetime
    db = get_connection()
    try:
        row = db.execute(
            "SELECT phone, expires_at FROM admin_sessions WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        expires_at = datetime.datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if expires_at < datetime.datetime.utcnow():
            db.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
            db.commit()
            return None
        return row["phone"]
    except Exception:
        return None
    finally:
        db.close()


def _cleanup_expired_sessions():
    """Remove expired admin sessions."""
    _ensure_admin_tables()
    import datetime
    db = get_connection()
    try:
        db.execute(
            "DELETE FROM admin_sessions WHERE expires_at < ?",
            (datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),)
        )
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


def require_admin(f):
    """Decorator: require admin session token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth_header[7:]
        phone = _validate_admin_session(token)
        if not phone or not _admin_exists_in_db(phone):
            return jsonify({"error": "Invalid admin token"}), 401
        request.admin_phone = phone
        return f(*args, **kwargs)
    return decorated

_tables_initialized = False


@app.before_request
def _startup_hook():
    global _tables_initialized
    ensure_db()
    _init_admin()
    if not _tables_initialized:
        _ensure_blacklist_table()
        _ensure_audit_table()
        _tables_initialized = True

# ========== Admin API: First-time Setup ==========
@app.route("/api/admin/setup", methods=["POST"])
@rate_limit(max_requests=3, window_seconds=300)
def admin_setup():
    """One-time setup: create default admin if no active admin exists."""
    data = request.get_json() or {}
    phone = (data.get("phone") or "13800138000").strip()
    password = data.get("password") or "admin123"
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 chars"}), 400
    db = get_connection()
    try:
        count = db.execute("SELECT COUNT(*) FROM admin_accounts WHERE is_active=1").fetchone()[0]
        if count > 0:
            return jsonify({"error": "admin already exists, use login"}), 403
        db.execute(
            "INSERT INTO admin_accounts (phone, password_hash, is_active) VALUES (?, ?, 1)",
            (phone, _hash_password(password))
        )
        db.commit()
        logger.info("First-time admin created: %s", phone)
        return jsonify({"status": "created", "phone": phone}), 201
    finally:
        db.close()


# ========== Admin API: Auth ==========
@app.route("/api/admin/login", methods=["POST"])
@rate_limit(max_requests=5, window_seconds=300)
def admin_login():
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    password = data.get("password", "")
    if not _verify_admin(phone, password):
        return jsonify({"error": "Invalid credentials"}), 401
    _cleanup_expired_sessions()
    token = _create_admin_session(phone)
    return jsonify({"token": token, "phone": phone, "is_admin": True})

# ========== Admin API: Stats ==========
@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def admin_stats():
    tenant_id = request.args.get("tenant_id", "").strip()
    db = get_connection()
    try:
        if tenant_id:
            user_count = db.execute("SELECT COUNT(*) FROM users WHERE id=?", (tenant_id,)).fetchone()[0]
            rule_count = db.execute("SELECT COUNT(*) FROM keyword_rules WHERE user_id=?", (tenant_id,)).fetchone()[0]
            history_count = db.execute("SELECT COUNT(*) FROM reply_history WHERE user_id=?", (tenant_id,)).fetchone()[0]
            today = _now_str()[:10]
            today_history = db.execute(
                "SELECT COUNT(*) FROM reply_history WHERE user_id=? AND created_at >= ?", (tenant_id, today)
            ).fetchone()[0]
            active_today = 1 if today_history > 0 else 0
        else:
            user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            rule_count = db.execute("SELECT COUNT(*) FROM keyword_rules").fetchone()[0]
            history_count = db.execute("SELECT COUNT(*) FROM reply_history").fetchone()[0]
            today = _now_str()[:10]
            today_history = db.execute(
                "SELECT COUNT(*) FROM reply_history WHERE created_at >= ?", (today,)
            ).fetchone()[0]
            active_today = db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM reply_history WHERE created_at >= ?", (today,)
            ).fetchone()[0]
    finally:
        db.close()
    return jsonify({
        "user_count": user_count,
        "rule_count": rule_count,
        "history_count": history_count,
        "today_history": today_history,
        "active_today": active_today,
        "tenant_id": tenant_id or "_global",
    })

@app.route("/api/admin/recent-tenants", methods=["GET"])
@require_admin
def admin_recent_tenants():
    tenant_id = request.args.get("tenant_id", "").strip()
    db = get_connection()
    try:
        if tenant_id:
            rows = db.execute(
                "SELECT u.id, u.name, u.phone, u.created_at FROM users u WHERE u.id=? LIMIT 10",
                (tenant_id,)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT u.id, u.name, u.phone, u.created_at FROM users u ORDER BY u.created_at DESC LIMIT 10"
            ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        db.close()

# ========== Admin API: Tenants ==========
@app.route("/api/admin/tenants", methods=["GET"])
@require_admin
def admin_tenants():
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 20, type=int), 100)
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "all").strip()
    offset = (page - 1) * page_size

    db = get_connection()
    try:
        query = "SELECT u.id, u.name, u.phone, u.created_at FROM users u"
        count_query = "SELECT COUNT(*) FROM users u"
        conditions = []
        params = []
        if search:
            conditions.append("(u.name LIKE ? OR u.id LIKE ? OR u.phone LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        if status == "active":
            conditions.append("u.created_at >= datetime('now', '-7 days')")
        elif status == "inactive":
            conditions.append("u.created_at < datetime('now', '-7 days')")
        if conditions:
            where = " WHERE " + " AND ".join(conditions)
            query += where
            count_query += where
        count_params = list(params)
        total = db.execute(count_query, count_params).fetchone()[0]
        query += " ORDER BY u.created_at DESC LIMIT ? OFFSET ?"
        params.extend([page_size, offset])
        rows = db.execute(query, params).fetchall()
        return jsonify({
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>", methods=["GET"])
@require_admin
def admin_get_tenant(tenant_id):
    db = get_connection()
    try:
        row = db.execute("SELECT * FROM users WHERE id = ?", (tenant_id,)).fetchone()
        if not row:
            return jsonify({"error": "Tenant not found"}), 404
        d = dict(row)
        d["rule_count"] = db.execute("SELECT COUNT(*) FROM keyword_rules WHERE user_id=?", (tenant_id,)).fetchone()[0]
        d["history_count"] = db.execute("SELECT COUNT(*) FROM reply_history WHERE user_id=?", (tenant_id,)).fetchone()[0]
        d["model_count"] = db.execute("SELECT COUNT(*) FROM model_configs WHERE user_id=?", (tenant_id,)).fetchone()[0]
        return jsonify(d)
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>", methods=["PUT"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_update_tenant(tenant_id):
    data = request.get_json() or {}
    name = data.get("name")
    db = get_connection()
    try:
        row = db.execute("SELECT id FROM users WHERE id=?", (tenant_id,)).fetchone()
        if not row:
            return jsonify({"error": "Tenant not found"}), 404
        if name is not None:
            name = str(name).strip()
            if len(name) > 200:
                return jsonify({"error": "name too long (max 200 chars)"}), 400
            db.execute("UPDATE users SET name=? WHERE id=?", (name, tenant_id))
        db.commit()
        _log_audit(request.admin_phone, "update_tenant", "tenant", tenant_id)
    finally:
        db.close()
    return jsonify({"status": "ok"})

# ========== Admin API: Tenant Default Model ==========
@app.route("/api/admin/tenants/_global/default-model", methods=["GET"])
@require_admin
def admin_get_global_default_model():
    # Return a global default model config
    db = get_connection()
    try:
        row = db.execute("SELECT * FROM model_configs WHERE user_id='_global' AND is_default=1 AND enabled=1 LIMIT 1").fetchone()
        if row:
            return jsonify(_model_to_dict(ModelConfig(**{k: row[k] for k in row.keys()})))
        return jsonify({})
    finally:
        db.close()

@app.route("/api/admin/tenants/_global/default-model", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_save_global_default_model():
    data = request.get_json() or {}
    # Ensure the _global pseudo-device exists
    db = get_connection()
    row = db.execute("SELECT id FROM devices WHERE id='_global'").fetchone()
    if not row:
        db.execute(
            "INSERT INTO devices (id, token, name, platform) VALUES ('_global', '_global_token', 'Global', 'system')"
        )
        db.commit()
    db.close()
    config = ModelConfig(
        user_id="_global",
        name=data.get("name", "global-default"),
        model_type=data.get("model_type", "OPENAI"),
        model=data.get("model", "gpt-4o"),
        api_key=data.get("api_key", ""),
        api_endpoint=data.get("api_endpoint", ""),
        temperature=data.get("temperature", 0.7),
        max_tokens=data.get("max_tokens", 2000),
        is_default=True,
        enabled=data.get("enabled", True),
    )
    repo = SqliteModelRepository()
    existing = repo.get_default("_global")
    if existing:
        config.id = existing.id
        repo.update(config)
    else:
        repo.create(config)
    return jsonify(_model_to_dict(config))

@app.route("/api/admin/tenants/<tenant_id>/default-model", methods=["GET"])
@require_admin
def admin_get_tenant_default_model(tenant_id):
    repo = SqliteModelRepository()
    config = repo.get_default(tenant_id)
    if config:
        return jsonify(_model_to_dict(config))
    return jsonify({})

@app.route("/api/admin/tenants/<tenant_id>/default-model", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_save_tenant_default_model(tenant_id):
    data = request.get_json() or {}
    config = ModelConfig(
        user_id=tenant_id,
        name=data.get("name", "default"),
        model_type=data.get("model_type", "OPENAI"),
        model=data.get("model", "gpt-4o"),
        api_key=data.get("api_key", ""),
        api_endpoint=data.get("api_endpoint", ""),
        temperature=data.get("temperature", 0.7),
        max_tokens=data.get("max_tokens", 2000),
        is_default=True,
        enabled=data.get("enabled", True),
    )
    repo = SqliteModelRepository()
    existing = repo.get_default(tenant_id)
    if existing:
        config.id = existing.id
        repo.update(config)
    else:
        repo.create(config)
    return jsonify(_model_to_dict(config))

@app.route("/api/admin/tenants/<tenant_id>/default-model", methods=["DELETE"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_delete_tenant_default_model(tenant_id):
    db = get_connection()
    try:
        db.execute("DELETE FROM model_configs WHERE user_id=? AND is_default=1", (tenant_id,))
        db.commit()
    finally:
        db.close()
    return jsonify({"status": "deleted"})

# ========== Admin API: Tenant Rules (proxy) ==========
@app.route("/api/admin/tenants/<tenant_id>/rules", methods=["GET"])
@require_admin
def admin_get_tenant_rules(tenant_id):
    repo = SqliteRuleRepository()
    rules = repo.get_by_device(tenant_id)
    return jsonify([_rule_to_dict(r) for r in rules])

@app.route("/api/admin/tenants/<tenant_id>/rules", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_create_tenant_rule(tenant_id):
    data = request.get_json() or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400
    if len(keyword) > 500:
        return jsonify({"error": "keyword too long (max 500 chars)"}), 400
    match_type = data.get("match_type", "CONTAINS")
    if match_type not in ("CONTAINS", "EXACT", "STARTS_WITH", "ENDS_WITH", "REGEX"):
        return jsonify({"error": "invalid match_type"}), 400
    priority = data.get("priority", 0)
    if not isinstance(priority, int) or priority < 0 or priority > 100:
        return jsonify({"error": "priority must be 0-100"}), 400
    target_names = data.get("target_names", [])
    if not isinstance(target_names, list):
        return jsonify({"error": "target_names must be a list"}), 400
    rule = KeywordRule(
        user_id=tenant_id,
        keyword=keyword,
        match_type=match_type,
        reply_template=data.get("reply_template", ""),
        category=data.get("category", ""),
        target_type=data.get("target_type", "ALL"),
        target_names=target_names,
        priority=priority,
        enabled=data.get("enabled", True),
    )
    repo = SqliteRuleRepository()
    created = repo.create(rule)
    return jsonify(_rule_to_dict(created)), 201

@app.route("/api/admin/tenants/<tenant_id>/rules/<int:rule_id>", methods=["PUT"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_update_tenant_rule(tenant_id, rule_id):
    repo = SqliteRuleRepository()
    existing = repo.get_by_id(rule_id, tenant_id)
    if not existing:
        return jsonify({"error": "Rule not found"}), 404
    data = request.get_json() or {}
    rule = KeywordRule(
        id=rule_id, user_id=tenant_id,
        keyword=data.get("keyword", existing.keyword).strip(),
        match_type=data.get("match_type", existing.match_type),
        reply_template=data.get("reply_template", existing.reply_template),
        category=data.get("category", existing.category),
        target_type=data.get("target_type", existing.target_type),
        target_names=data.get("target_names", existing.target_names),
        priority=data.get("priority", existing.priority),
        enabled=data.get("enabled", existing.enabled),
    )
    updated = repo.update(rule)
    return jsonify(_rule_to_dict(updated))

@app.route("/api/admin/tenants/<tenant_id>/rules/<int:rule_id>", methods=["DELETE"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_delete_tenant_rule(tenant_id, rule_id):
    repo = SqliteRuleRepository()
    deleted = repo.delete(rule_id, tenant_id)
    if not deleted:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"status": "deleted", "id": rule_id})

@app.route("/api/admin/tenants/<tenant_id>/rules/batch", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_batch_import_tenant_rules(tenant_id):
    data = request.get_json() or {}
    rules_data = data.get("rules", [])
    if len(rules_data) > 1000:
        return jsonify({"error": "too many rules (max 1000)"}), 400
    mode = data.get("mode", "append")
    rules = [KeywordRule(
        user_id=tenant_id, keyword=r.get("keyword", ""),
        match_type=r.get("match_type", "CONTAINS"), reply_template=r.get("reply_template", ""),
        category=r.get("category", ""), target_type=r.get("target_type", "ALL"),
        target_names=r.get("target_names", []), priority=r.get("priority", 0),
    ) for r in rules_data]
    repo = SqliteRuleRepository()
    count = repo.batch_create(rules, tenant_id, mode)
    total = len(repo.get_by_device(tenant_id))
    return jsonify({"status": "ok", "imported": count, "total": total})

# ========== Admin API: Blacklist ==========
# Blacklist table
_blacklist_initialized = False
def _ensure_blacklist_table():
    global _blacklist_initialized
    if not _blacklist_initialized:
        db = get_connection()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                type TEXT DEFAULT 'KEYWORD',
                value TEXT NOT NULL,
                description TEXT DEFAULT '',
                package_name TEXT,
                is_enabled INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_blacklist_user ON blacklist(user_id);
        """)
        db.commit()
        db.close()
        _blacklist_initialized = True


def _blacklist_hook():
    _ensure_blacklist_table()

def _blacklist_to_dict(row):
    if not row:
        return {}
    d = dict(row)
    d["is_enabled"] = bool(d.get("is_enabled", 1))
    return d

@app.route("/api/admin/tenants/<tenant_id>/blacklist", methods=["GET"])
@require_admin
def admin_get_blacklist(tenant_id):
    db = get_connection()
    try:
        rows = db.execute("SELECT * FROM blacklist WHERE user_id=? ORDER BY created_at DESC", (tenant_id,)).fetchall()
        return jsonify([_blacklist_to_dict(r) for r in rows])
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>/blacklist", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_add_blacklist(tenant_id):
    data = request.get_json() or {}
    value = data.get("value", "").strip()
    if not value:
        return jsonify({"error": "value is required"}), 400
    db = get_connection()
    try:
        cur = db.execute(
            "INSERT INTO blacklist (user_id, type, value, description, package_name, is_enabled) VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, data.get("type", "KEYWORD"), value, data.get("description", ""),
             data.get("package_name"), 1 if data.get("is_enabled", True) else 0)
        )
        db.commit()
        return jsonify({"id": cur.lastrowid, "status": "created"}), 201
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>/blacklist/<int:bid>", methods=["PUT"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_update_blacklist(tenant_id, bid):
    data = request.get_json() or {}
    db = get_connection()
    try:
        row = db.execute("SELECT id FROM blacklist WHERE id=? AND user_id=?", (bid, tenant_id)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        db.execute(
            "UPDATE blacklist SET type=?, value=?, description=?, package_name=?, is_enabled=? WHERE id=? AND user_id=?",
            (data.get("type", "KEYWORD"), data.get("value", ""), data.get("description", ""),
             data.get("package_name"), 1 if data.get("is_enabled", True) else 0, bid, tenant_id)
        )
        db.commit()
        return jsonify({"status": "updated"})
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>/blacklist/<int:bid>", methods=["DELETE"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_delete_blacklist(tenant_id, bid):
    db = get_connection()
    try:
        cur = db.execute("DELETE FROM blacklist WHERE id=? AND user_id=?", (bid, tenant_id))
        db.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"status": "deleted"})
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>/blacklist/clear", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_clear_blacklist(tenant_id):
    db = get_connection()
    try:
        db.execute("DELETE FROM blacklist WHERE user_id=?", (tenant_id,))
        db.commit()
        return jsonify({"status": "cleared"})
    finally:
        db.close()

# ========== Admin API: History ==========
@app.route("/api/admin/tenants/<tenant_id>/history", methods=["GET"])
@require_admin
def admin_get_tenant_history(tenant_id):
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 20, type=int), 100)
    source = request.args.get("source", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    offset = (page - 1) * page_size

    db = get_connection()
    try:
        query = "SELECT * FROM reply_history WHERE user_id = ?"
        count_query = "SELECT COUNT(*) FROM reply_history WHERE user_id = ?"
        params = [tenant_id]
        if source:
            query += " AND source = ?"
            count_query += " AND source = ?"
            params.append(source)
        if date_from:
            query += " AND created_at >= ?"
            count_query += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND created_at <= ?"
            count_query += " AND created_at <= ?"
            params.append(date_to)
        total = db.execute(count_query, list(params)).fetchone()[0]
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([page_size, offset])
        rows = db.execute(query, params).fetchall()
        return jsonify({
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    finally:
        db.close()

# ========== Admin API: Models ==========
@app.route("/api/admin/tenants/<tenant_id>/models", methods=["GET"])
@require_admin
def admin_get_tenant_models(tenant_id):
    repo = SqliteModelRepository()
    models = repo.get_by_device(tenant_id)
    return jsonify([_model_to_dict(m) for m in models])

@app.route("/api/admin/tenants/<tenant_id>/models", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_create_tenant_model(tenant_id):
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    temperature = data.get("temperature", 0.7)
    if not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2:
        return jsonify({"error": "temperature must be 0-2"}), 400
    max_tokens = data.get("max_tokens", 2000)
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 32000:
        return jsonify({"error": "max_tokens must be 1-32000"}), 400
    config = ModelConfig(
        user_id=tenant_id,
        name=name,
        model_type=data.get("model_type", "OPENAI"),
        model=data.get("model", "gpt-4o"),
        api_key=data.get("api_key", ""),
        api_endpoint=data.get("api_endpoint", ""),
        temperature=temperature,
        max_tokens=max_tokens,
        is_default=data.get("is_default", False),
        enabled=data.get("enabled", True),
    )
    repo = SqliteModelRepository()
    created = repo.create(config)
    return jsonify(_model_to_dict(created)), 201

@app.route("/api/admin/tenants/<tenant_id>/models/<int:model_id>", methods=["PUT"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_update_tenant_model(tenant_id, model_id):
    repo = SqliteModelRepository()
    existing = repo.get_by_id(model_id, tenant_id)
    if not existing:
        return jsonify({"error": "Model not found"}), 404
    data = request.get_json() or {}
    config = ModelConfig(
        id=model_id, user_id=tenant_id,
        name=data.get("name", existing.name).strip(),
        model_type=data.get("model_type", existing.model_type),
        model=data.get("model", existing.model),
        api_key=data.get("api_key", existing.api_key),
        api_endpoint=data.get("api_endpoint", existing.api_endpoint),
        temperature=data.get("temperature", existing.temperature),
        max_tokens=data.get("max_tokens", existing.max_tokens),
        is_default=data.get("is_default", existing.is_default),
        enabled=data.get("enabled", existing.enabled),
    )
    updated = repo.update(config)
    return jsonify(_model_to_dict(updated))

@app.route("/api/admin/tenants/<tenant_id>/models/<int:model_id>", methods=["DELETE"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_delete_tenant_model(tenant_id, model_id):
    repo = SqliteModelRepository()
    deleted = repo.delete(model_id, tenant_id)
    if not deleted:
        return jsonify({"error": "Model not found"}), 404
    return jsonify({"status": "deleted", "id": model_id})

# ========== Admin API: Feedback ==========
@app.route("/api/admin/tenants/<tenant_id>/feedback", methods=["GET"])
@require_admin
def admin_get_tenant_feedback(tenant_id):
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 20, type=int), 100)
    action = request.args.get("action", "").strip()
    offset = (page - 1) * page_size
    db = get_connection()
    try:
        query = "SELECT * FROM feedback WHERE user_id = ?"
        count_query = "SELECT COUNT(*) FROM feedback WHERE user_id = ?"
        params = [tenant_id]
        if action:
            query += " AND action = ?"
            count_query += " AND action = ?"
            params.append(action)
        total = db.execute(count_query, list(params)).fetchone()[0]
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([page_size, offset])
        rows = db.execute(query, params).fetchall()
        return jsonify({
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    finally:
        db.close()

# ========== Admin API: Metrics ==========
@app.route("/api/admin/tenants/<tenant_id>/metrics", methods=["GET"])
@require_admin
def admin_get_tenant_metrics(tenant_id):
    days = request.args.get("days", 7, type=int)
    days = max(1, min(days, 365))
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 30, type=int), 100)
    offset = (page - 1) * page_size
    repo = SqliteMetricsRepository()
    items = repo.get_by_device_and_date_range(tenant_id, days)
    total = len(items)
    items_page = items[offset:offset + page_size]
    return jsonify({
        "items": [{
            "id": m.id, "user_id": m.user_id, "date": m.date,
            "total_generated": m.total_generated, "total_accepted": m.total_accepted,
            "total_modified": m.total_modified, "total_rejected": m.total_rejected,
        } for m in items_page],
        "total": total,
        "page": page,
        "page_size": page_size,
    })

@app.route("/api/admin/tenants/<tenant_id>/metrics/summary", methods=["GET"])
@require_admin
def admin_get_tenant_metrics_summary(tenant_id):
    days = request.args.get("days", 7, type=int)
    repo = SqliteMetricsRepository()
    items = repo.get_by_device_and_date_range(tenant_id, days)
    total = sum(m.total_generated for m in items)
    accepted = sum(m.total_accepted for m in items)
    modified = sum(m.total_modified for m in items)
    rejected = sum(m.total_rejected for m in items)
    return jsonify({
        "period_days": days,
        "total_generated": total,
        "total_accepted": accepted,
        "total_modified": modified,
        "total_rejected": rejected,
    })

# ========== Admin API: Audit Log ==========
_audit_log_initialized = False
def _ensure_audit_table():
    global _audit_log_initialized
    if not _audit_log_initialized:
        db = get_connection()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_phone TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT DEFAULT '',
                target_id TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
        """)
        db.commit()
        db.close()
        _audit_log_initialized = True


def _log_audit(admin_phone, action, target_type="", target_id="", detail=""):
    db = None
    try:
        db = get_connection()
        db.execute(
            "INSERT INTO audit_log (admin_phone, action, target_type, target_id, detail) VALUES (?, ?, ?, ?, ?)",
            (admin_phone, action, target_type, target_id, detail)
        )
        db.commit()
    except Exception as e:
        logger.error("Audit log failed: %s", e, exc_info=True)
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass

@app.route("/api/admin/audit-log", methods=["GET"])
@require_admin
def admin_audit_log():
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 20, type=int), 100)
    action = request.args.get("action", "").strip()
    tenant_id = request.args.get("tenant_id", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    offset = (page - 1) * page_size
    db = get_connection()
    try:
        query = "SELECT * FROM audit_log WHERE 1=1"
        count_query = "SELECT COUNT(*) FROM audit_log WHERE 1=1"
        params = []
        if action:
            query += " AND action = ?"
            count_query += " AND action = ?"
            params.append(action)
        if tenant_id:
            query += " AND (target_id = ? OR detail LIKE ?)"
            count_query += " AND (target_id = ? OR detail LIKE ?)"
            params.append(tenant_id)
            params.append(f"%{tenant_id}%")
        if date_from:
            query += " AND created_at >= ?"
            count_query += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND created_at <= ?"
            count_query += " AND created_at <= ?"
            params.append(date_to)
        total = db.execute(count_query, list(params)).fetchone()[0]
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([page_size, offset])
        rows = db.execute(query, params).fetchall()
        return jsonify({
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        })
    finally:
        db.close()

# ========== Admin API: Admin Management ==========
@app.route("/api/admin/admins", methods=["GET"])
@require_admin
def admin_list_admins():
    return jsonify(_get_all_admins())

@app.route("/api/admin/admins", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_create_admin():
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    password = data.get("password", "")
    if not phone or not password:
        return jsonify({"error": "phone and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 chars"}), 400
    db = get_connection()
    try:
        row = db.execute("SELECT phone FROM admin_accounts WHERE phone=?", (phone,)).fetchone()
        if row:
            return jsonify({"error": "admin already exists"}), 409
        db.execute(
            "INSERT INTO admin_accounts (phone, password_hash, is_active) VALUES (?, ?, ?)",
            (phone, _hash_password(password), 1 if data.get("is_active", True) else 0)
        )
        db.commit()
    finally:
        db.close()
    _log_audit(request.admin_phone, "create_admin", "admin", phone)
    return jsonify({"status": "created", "phone": phone}), 201

@app.route("/api/admin/admins/<phone>", methods=["PUT"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_update_admin(phone):
    data = request.get_json() or {}
    db = get_connection()
    try:
        row = db.execute("SELECT phone FROM admin_accounts WHERE phone=?", (phone,)).fetchone()
        if not row:
            return jsonify({"error": "Admin not found"}), 404
        if "is_active" in data:
            db.execute("UPDATE admin_accounts SET is_active=? WHERE phone=?", (1 if data["is_active"] else 0, phone))
        if "password" in data:
            if len(data["password"]) < 6:
                return jsonify({"error": "password must be at least 6 chars"}), 400
            db.execute("UPDATE admin_accounts SET password_hash=? WHERE phone=?", (_hash_password(data["password"]), phone))
        db.commit()
    finally:
        db.close()
    _log_audit(request.admin_phone, "update_admin", "admin", phone)
    return jsonify({"status": "updated"})

@app.route("/api/admin/admins/<phone>", methods=["DELETE"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_delete_admin(phone):
    db = get_connection()
    try:
        row = db.execute("SELECT phone FROM admin_accounts WHERE phone=?", (phone,)).fetchone()
        if not row:
            return jsonify({"error": "Admin not found"}), 404
        if phone == request.admin_phone:
            return jsonify({"error": "Cannot delete yourself"}), 400
        db.execute("DELETE FROM admin_accounts WHERE phone=?", (phone,))
        db.commit()
    finally:
        db.close()
    # Clean up tokens belonging to the deleted admin
    tokens_to_remove = [t for t, p in _admin_tokens.items() if p == phone]
    for t in tokens_to_remove:
        del _admin_tokens[t]
    _log_audit(request.admin_phone, "delete_admin", "admin", phone)
    return jsonify({"status": "deleted"})

# ========== Admin API: Agent Status & Routing (SQLite persisted) ==========

def _load_routing_config_from_db():
    """Load routing_config from DB into memory on startup."""
    db = get_connection()
    try:
        rows = db.execute("SELECT `key`, value FROM api_routing_config").fetchall()
        cfg = {}
        for r in rows:
            try:
                cfg[r["key"]] = json.loads(r["value"])
            except Exception:
                cfg[r["key"]] = r["value"]
        return cfg
    except Exception:
        return {}
    finally:
        db.close()

def _save_routing_config_to_db(key, value):
    """Persist a single routing config key to DB (no-op if table not yet created)."""
    db = get_connection()
    try:
        db.execute(
            "INSERT OR REPLACE INTO api_routing_config (`key`, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), _now_str())
        )
        db.commit()
    except Exception:
        pass  # Table may not exist yet during module load
    finally:
        db.close()

# Initialize routing config from DB with defaults (module-level, runs at import time)
_DEFAULT_ROUTING_CONFIG = {"strategy": "skill_first", "fallback_to_ai": True, "max_queue_size": 50, "timeout_seconds": 300}
_routing_config = _load_routing_config_from_db()
for _k, _v in _DEFAULT_ROUTING_CONFIG.items():
    if _k not in _routing_config:
        _routing_config[_k] = _v
        _save_routing_config_to_db(_k, _v)

@app.route("/api/agent/status", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_set_agent_status():
    data = request.get_json() or {}
    phone = data.get("agent_phone", "").strip()
    if not phone:
        return jsonify({"error": "agent_phone required"}), 400
    status = data.get("status", "online")
    if status not in ("online", "offline", "busy", "away"):
        return jsonify({"error": "invalid status"}), 400
    db = get_connection()
    try:
        db.execute(
            """INSERT OR REPLACE INTO agent_status (phone, agent_name, status, max_concurrent, tenant_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (phone, data.get("agent_name", phone), status,
             data.get("max_concurrent", 5),
             data.get("tenant_id", ""),
             _now_str())
        )
        db.commit()
    finally:
        db.close()
    return jsonify({"status": "ok"})

@app.route("/api/agent/skills", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_set_agent_skills():
    data = request.get_json() or {}
    phone = data.get("agent_phone", "").strip()
    if not phone:
        return jsonify({"error": "agent_phone required"}), 400
    skills = data.get("skills", [])
    if not isinstance(skills, list):
        return jsonify({"error": "skills must be a list"}), 400
    db = get_connection()
    try:
        db.execute("DELETE FROM agent_skills WHERE agent_phone=?", (phone,))
        for s in skills:
            db.execute(
                "INSERT INTO agent_skills (agent_phone, skill_tag, proficiency) VALUES (?, ?, ?)",
                (phone, s.get("skill_tag", ""), s.get("proficiency", 5))
            )
        db.commit()
    finally:
        db.close()
    return jsonify({"status": "ok"})

@app.route("/api/admin/tenants/<tenant_id>/agents", methods=["GET"])
@require_admin
def admin_get_tenant_agents(tenant_id):
    db = get_connection()
    try:
        rows = db.execute(
            "SELECT * FROM agent_status WHERE tenant_id=? ORDER BY updated_at DESC",
            (tenant_id,)
        ).fetchall()
        agents = []
        for r in rows:
            agent = dict(r)
            skill_rows = db.execute(
                "SELECT skill_tag, proficiency FROM agent_skills WHERE agent_phone=?",
                (agent["phone"],)
            ).fetchall()
            agent["skills"] = [dict(s) for s in skill_rows]
            agents.append(agent)
        return jsonify({"agents": agents})
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>/sessions", methods=["GET"])
@require_admin
def admin_get_tenant_sessions(tenant_id):
    db = get_connection()
    try:
        rows = db.execute(
            "SELECT * FROM sessions WHERE tenant_id=? ORDER BY created_at DESC LIMIT 200",
            (tenant_id,)
        ).fetchall()
        sessions = [dict(r) for r in rows]
        total_row = db.execute(
            "SELECT COUNT(*) as cnt FROM sessions WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        total = total_row["cnt"] if total_row else 0
        return jsonify({"sessions": sessions, "total": total})
    finally:
        db.close()

@app.route("/api/routing/config", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_update_routing_config():
    data = request.get_json() or {}
    VALID_STRATEGIES = {"skill_first", "round_robin", "least_busy", "ai_first"}
    if "strategy" in data and data["strategy"] not in VALID_STRATEGIES:
        return jsonify({"error": f"invalid strategy: must be one of {VALID_STRATEGIES}"}), 400
    if "max_queue_size" in data:
        mq = data["max_queue_size"]
        if not isinstance(mq, int) or mq < 1 or mq > 10000:
            return jsonify({"error": "max_queue_size must be 1-10000"}), 400
    if "timeout_seconds" in data:
        ts = data["timeout_seconds"]
        if not isinstance(ts, int) or ts < 1 or ts > 3600:
            return jsonify({"error": "timeout_seconds must be 1-3600"}), 400
    for key in ("strategy", "fallback_to_ai", "max_queue_size", "timeout_seconds"):
        if key in data:
            _routing_config[key] = data[key]
            _save_routing_config_to_db(key, data[key])
    return jsonify({"status": "ok", "config": _routing_config})

@app.route("/api/admin/tenants/<tenant_id>/routing/config", methods=["GET"])
@require_admin
def admin_get_routing_config(tenant_id):
    return jsonify(_routing_config)

@app.route("/api/conversation/<int:session_id>/close", methods=["POST"])
@rate_limit(max_requests=20, window_seconds=60)
@require_admin
def admin_close_conversation(session_id):
    data = request.get_json() or {}
    db = get_connection()
    try:
        row = db.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            return jsonify({"error": "Session not found"}), 404
        db.execute("UPDATE sessions SET status='closed', closed_at=? WHERE id=?", (_now_str(), session_id))
        db.commit()
    finally:
        db.close()
    return jsonify({"status": "ok"})

# ========== Admin API: Change Password ==========
@app.route("/api/auth/change_password", methods=["POST"])
@rate_limit(max_requests=5, window_seconds=300)
@require_admin
def admin_change_password():
    data = request.get_json() or {}
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")
    if not old_password or not new_password:
        return jsonify({"error": "old_password and new_password required"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "new_password must be at least 6 chars"}), 400
    phone = request.admin_phone
    db = get_connection()
    try:
        row = db.execute("SELECT password_hash FROM admin_accounts WHERE phone=?", (phone,)).fetchone()
        if not row or row["password_hash"] != _hash_password(old_password):
            return jsonify({"error": "Invalid old password"}), 401
        db.execute("UPDATE admin_accounts SET password_hash=? WHERE phone=?", (_hash_password(new_password), phone))
        db.commit()
    finally:
        db.close()
    return jsonify({"status": "ok"})

# ========== Admin API: Tenant Style Config ==========
@app.route("/api/admin/tenants/<tenant_id>/style", methods=["GET"])
@require_admin
def admin_get_tenant_style(tenant_id):
    db = get_connection()
    try:
        row = db.execute("SELECT * FROM tenant_style_config WHERE user_id=?", (tenant_id,)).fetchone()
        if not row:
            return jsonify({
                "user_id": tenant_id, "theme": "light", "primary_color": "#1976D2",
                "accent_color": "#FF4081", "font_size": "medium", "bubble_style": "rounded",
                "avatar_enabled": 1, "show_timestamp": 1, "send_sound": 1, "custom_css": "",
            })
        return jsonify(dict(row))
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>/style", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_update_tenant_style(tenant_id):
    data = request.get_json() or {}
    THEME_CHOICES = {"light", "dark", "auto"}
    FONT_CHOICES = {"small", "medium", "large"}
    BUBBLE_CHOICES = {"rounded", "square", "bubble"}
    theme = data.get("theme", "light")
    if theme not in THEME_CHOICES:
        theme = "light"
    font_size = data.get("font_size", "medium")
    if font_size not in FONT_CHOICES:
        font_size = "medium"
    bubble_style = data.get("bubble_style", "rounded")
    if bubble_style not in BUBBLE_CHOICES:
        bubble_style = "rounded"
    db = get_connection()
    try:
        db.execute(
            """INSERT OR REPLACE INTO tenant_style_config
               (user_id, theme, primary_color, accent_color, font_size, bubble_style,
                avatar_enabled, show_timestamp, send_sound, custom_css, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tenant_id, theme, data.get("primary_color", "#1976D2"),
             data.get("accent_color", "#FF4081"), font_size, bubble_style,
             1 if data.get("avatar_enabled", True) else 0,
             1 if data.get("show_timestamp", True) else 0,
             1 if data.get("send_sound", True) else 0,
             data.get("custom_css", ""), _now_str())
        )
        db.commit()
    finally:
        db.close()
    _log_audit(request.admin_phone, "update_style", "tenant", tenant_id)
    return jsonify({"status": "ok"})

# ========== Admin API: Tenant App Config ==========
@app.route("/api/admin/tenants/<tenant_id>/app-config", methods=["GET"])
@require_admin
def admin_get_tenant_app_config(tenant_id):
    db = get_connection()
    try:
        row = db.execute("SELECT * FROM tenant_app_config WHERE user_id=?", (tenant_id,)).fetchone()
        if not row:
            return jsonify({
                "user_id": tenant_id, "app_name": "客服小秘",
                "welcome_message": "您好，请问有什么可以帮您？",
                "offline_message": "当前无客服在线，请稍后再试。",
                "auto_reply_enabled": 1, "notification_enabled": 1, "voice_enabled": 0,
                "language": "zh-CN", "session_timeout": 300, "max_queue_size": 50,
                "file_upload_enabled": 1,
            })
        return jsonify(dict(row))
    finally:
        db.close()

@app.route("/api/admin/tenants/<tenant_id>/app-config", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
@require_admin
def admin_update_tenant_app_config(tenant_id):
    data = request.get_json() or {}
    session_timeout = data.get("session_timeout", 300)
    try:
        session_timeout = max(30, min(int(session_timeout), 3600))
    except (ValueError, TypeError):
        session_timeout = 300
    max_queue_size = data.get("max_queue_size", 50)
    try:
        max_queue_size = max(1, min(int(max_queue_size), 500))
    except (ValueError, TypeError):
        max_queue_size = 50
    db = get_connection()
    try:
        db.execute(
            """INSERT OR REPLACE INTO tenant_app_config
               (user_id, app_name, welcome_message, offline_message,
                auto_reply_enabled, notification_enabled, voice_enabled,
                language, session_timeout, max_queue_size, file_upload_enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tenant_id, data.get("app_name", "客服小秘"),
             data.get("welcome_message", "您好，请问有什么可以帮您？"),
             data.get("offline_message", "当前无客服在线，请稍后再试。"),
             1 if data.get("auto_reply_enabled", True) else 0,
             1 if data.get("notification_enabled", True) else 0,
             1 if data.get("voice_enabled", False) else 0,
             data.get("language", "zh-CN"),
             session_timeout, max_queue_size,
             1 if data.get("file_upload_enabled", True) else 0,
             _now_str())
        )
        db.commit()
    finally:
        db.close()
    _log_audit(request.admin_phone, "update_app_config", "tenant", tenant_id)
    return jsonify({"status": "ok"})

# ========== Admin API: Tenant Backup Management ==========
@app.route("/api/admin/tenants/<tenant_id>/backup", methods=["GET"])
@require_admin
def admin_export_tenant_backup(tenant_id):
    """Export full backup data for a tenant."""
    user_id = tenant_id
    rule_repo = SqliteRuleRepository()
    model_repo = SqliteModelRepository()
    history_repo = SqliteHistoryRepository()
    feedback_repo = SqliteFeedbackRepository()
    metrics_repo = SqliteMetricsRepository()

    db = get_connection()
    try:
        row = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return jsonify({"error": "Tenant not found"}), 404
    finally:
        db.close()

    rules = rule_repo.get_by_user(user_id)
    models = model_repo.get_by_user(user_id)
    history_items, _ = history_repo.get_by_user(user_id, 5000, 0)
    feedback_items = feedback_repo.get_by_user(user_id, 5000, 0)
    metrics = metrics_repo.get_by_user_and_date_range(user_id, 365)

    blacklist_items = []
    try:
        db = get_connection()
        bl_rows = db.execute("SELECT * FROM blacklist WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        blacklist_items = [dict(r) for r in bl_rows]
        db.close()
    except Exception:
        pass

    return jsonify({
        "version": 2,
        "user_id": user_id,
        "exported_at": _now_str(),
        "rules": [_rule_to_dict(r) for r in rules],
        "models": [_model_to_dict(m) for m in models],
        "history": [{"id": h.id, "original_message": h.original_message,
                      "reply_content": h.reply_content, "source": h.source,
                      "model_used": h.model_used, "confidence": h.confidence,
                      "response_time_ms": h.response_time_ms, "platform": h.platform,
                      "customer_name": h.customer_name, "house_name": h.house_name,
                      "created_at": h.created_at} for h in history_items],
        "feedback": [{"id": f.id, "reply_history_id": f.reply_history_id,
                       "action": f.action, "modified_text": f.modified_text,
                       "rating": f.rating, "comment": f.comment, "created_at": f.created_at}
                      for f in feedback_items],
        "metrics": [{"date": m.date, "total_generated": m.total_generated,
                      "total_accepted": m.total_accepted, "total_modified": m.total_modified,
                      "total_rejected": m.total_rejected} for m in metrics],
        "blacklist": blacklist_items,
    })


@app.route("/api/admin/tenants/<tenant_id>/backup/restore", methods=["POST"])
@rate_limit(max_requests=5, window_seconds=60)
@require_admin
def admin_restore_tenant_backup(tenant_id):
    """Restore tenant data from a backup JSON payload."""
    data = request.get_json() or {}
    backup = data.get("backup")
    if not isinstance(backup, dict):
        return jsonify({"error": "backup must be a JSON object"}), 400

    user_id = tenant_id
    db = get_connection()
    try:
        row = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return jsonify({"error": "Tenant not found"}), 404
    finally:
        db.close()

    restored = {"rules": 0, "models": 0, "blacklist": 0}

    rules_data = backup.get("rules", [])
    if rules_data and isinstance(rules_data, list):
        parsed_rules = []
        for r in rules_data:
            target_names = r.get("target_names", [])
            if isinstance(target_names, str):
                try:
                    target_names = json.loads(target_names)
                except json.JSONDecodeError:
                    target_names = []
            if not isinstance(target_names, list):
                target_names = []
            parsed_rules.append(KeywordRule(
                user_id=user_id, keyword=r.get("keyword", ""),
                match_type=r.get("match_type", "CONTAINS"), reply_template=r.get("reply_template", ""),
                category=r.get("category", ""), target_type=r.get("target_type", "ALL"),
                target_names=target_names, priority=r.get("priority", 0),
                enabled=bool(r.get("enabled", True)),
            ))
        if parsed_rules:
            SqliteRuleRepository().batch_create(parsed_rules, user_id, "override")
            restored["rules"] = len(parsed_rules)

    models_data = backup.get("models", [])
    if models_data and isinstance(models_data, list):
        db = get_connection()
        try:
            db.execute("DELETE FROM model_configs WHERE user_id=?", (user_id,))
            db.commit()
        finally:
            db.close()
        count = 0
        for m_data in models_data:
            try:
                config = ModelConfig(
                    user_id=user_id, name=m_data.get("name", ""),
                    model_type=m_data.get("model_type", "OPENAI"), model=m_data.get("model", ""),
                    api_key=m_data.get("api_key", ""), api_endpoint=m_data.get("api_endpoint", ""),
                    temperature=m_data.get("temperature", 0.7), max_tokens=m_data.get("max_tokens", 2000),
                    is_default=bool(m_data.get("is_default", False)), enabled=bool(m_data.get("enabled", True)),
                )
                SqliteModelRepository().create(config)
                count += 1
            except Exception:
                pass
        restored["models"] = count

    bl_data = backup.get("blacklist", [])
    if bl_data and isinstance(bl_data, list):
        db = get_connection()
        try:
            db.execute("DELETE FROM blacklist WHERE user_id=?", (user_id,))
            db.commit()
            for bl in bl_data:
                db.execute(
                    "INSERT INTO blacklist (user_id, type, value, description, package_name, is_enabled) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, bl.get("type", "KEYWORD"), bl.get("value", ""),
                     bl.get("description", ""), bl.get("package_name"),
                     1 if bl.get("is_enabled", True) else 0)
                )
            db.commit()
            restored["blacklist"] = len(bl_data)
        except Exception:
            pass
        finally:
            db.close()

    _log_audit(request.admin_phone, "restore_backup", "tenant", tenant_id)
    return jsonify({"status": "ok", "restored": restored})


# ========== Global Error Handlers ==========
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(403)
def forbidden(e):
    return jsonify({"error": "Forbidden"}), 403

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500

# ========== Admin Panel (registered as Blueprint at /admin) ==========
def _register_admin():
    """Register the admin Blueprint with the main Flask app."""
    import sys
    _admin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin")
    _added = False
    if _admin_dir not in sys.path:
        sys.path.insert(0, _admin_dir)
        _added = True
    try:
        import importlib.util
        for key in list(sys.modules.keys()):
            if key.startswith('admin.') or key == 'app' and sys.modules[key].__file__ and 'admin' in sys.modules[key].__file__:
                del sys.modules[key]
        _spec = importlib.util.spec_from_file_location(
            "_admin_app", os.path.join(_admin_dir, "app.py"))
        _admin_mod = importlib.util.module_from_spec(_spec)
        sys.modules["_admin_app"] = _admin_mod
        _spec.loader.exec_module(_admin_mod)
        _admin_bp = _admin_mod.admin_bp
        # Configure admin blueprint settings on the main app
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
        app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB
        # Wire up internal API client
        with app.test_request_context():
            app._api_test_client = app.test_client()
        os.environ["ADMIN_API_MODE"] = "internal"
        app.register_blueprint(_admin_bp)
        logger.info("Admin panel registered successfully at /admin")
        return True
    except Exception as e:
        logger.warning("Admin panel not registered: %s", e, exc_info=True)
        return False
    finally:
        if _added:
            sys.path.remove(_admin_dir)

_admin_registered = _register_admin()

application = app

@app.route("/_debug/admin-status")
@require_admin
def _debug_admin_status():
    """Debug endpoint to check if admin panel is registered. Protected by admin auth."""
    return jsonify({
        "admin_registered": _admin_registered,
        "admin_mode": os.environ.get("ADMIN_API_MODE", "not set"),
        "admin_routes": [str(r) for r in app.url_map.iter_rules() if "admin" in str(r)],
    })

# ========== 启动 ==========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)



import time
from datetime import datetime

# ========== Cloud Sync Compatibility Routes (added by refactor) ==========
# These routes provide backward compatibility with the legacy sync server (csBaby-server-py)
# so the Android client can use a single domain (api.agentai0.com) for all API calls.
# nginx routes /auth/* and /sync/* and /api/v1/backup/* to this container (8084).

@app.route("/sync/pull", methods=["GET"])
@require_auth


def _coerce_rows_sync_all(body):
    # Apply _coerce_rows to all list fields in body
    for k, v in body.items():
        if isinstance(v, list):
            body[k] = _coerce_rows(v)
    return body

def _coerce_rows(rows):
    """Cast types so Gson (Android client) parses correctly.
    - id: string -> int (Long). 异常 uuid 字符串(含 - _ 时)退化为 abs(hash) % 2^31
    - deleted/enabled: int 0/1 -> bool
    - snake_case keys -> camelCase (与客户端 SyncKeywordRule 等 @SerializedName 对齐)
    """
    import re as _re
    def _to_camel_keys(d):
        return {_re.sub(r'_([a-z])', lambda m: m.group(1).upper(), k): v for k, v in d.items()}
    out = []
    for r in rows or []:
        obj = {}
        for k, v in r.items():
            if k == "id":
                s = str(v).strip()
                if s.isdigit():
                    try:
                        obj[k] = int(s)
                    except Exception:
                        obj[k] = abs(hash(s)) % (2**31)
                else:
                    obj[k] = abs(hash(s)) % (2**31)
            elif k in ("deleted", "enabled") and isinstance(v, int):
                obj[k] = v != 0
            else:
                obj[k] = v
        out.append(_to_camel_keys(obj))
    return out


@app.route("/auth/refresh", methods=["POST"])
@require_auth
def auth_refresh():
    """Refresh access token via refreshToken.
    Request: {"refreshToken": "..."}
    Response: {userId, tenantId, token (new), refreshToken (new), expiresIn}
    """
    import jwt as _jwt
    data = request.get_json(force=True, silent=True) or {}
    refresh_token = data.get("refreshToken")
    if not refresh_token:
        return jsonify({"error": "Missing refreshToken"}), 400
    try:
        import os
        payload = _jwt.decode(refresh_token, os.environ.get('JWT_SECRET', 'x'), algorithms=["HS256"])
        user_id = payload.get("user_id")
        tenant_id = payload.get("tenant_id", user_id)
        if payload.get("type") != "refresh":
            return jsonify({"error": "Invalid token type"}), 401
    except Exception:
        return jsonify({"error": "Invalid refresh token"}), 401

    # Issue new tokens
    now = int(__import__("time").time())
    access_payload = {
        "user_id": user_id, "tenant_id": tenant_id, "type": "access",
        "iat": now, "exp": now + 24 * 60 * 60,
    }
    new_access = _jwt.encode(access_payload, os.environ.get('JWT_SECRET', 'x'), algorithm="HS256")
    refresh_payload = {
        "user_id": user_id, "tenant_id": tenant_id, "type": "refresh",
        "iat": now, "exp": now + 30 * 24 * 60 * 60,
    }
    new_refresh = _jwt.encode(refresh_payload, os.environ.get('JWT_SECRET', 'x'), algorithm="HS256")
    return jsonify({
        "userId": user_id, "tenantId": tenant_id,
        "token": new_access, "refreshToken": new_refresh,
        "expiresIn": 30 * 24 * 60 * 60,
    })


@app.route("/sync/all", methods=["GET"])
@require_auth
def sync_all():
    """Full sync: return tenant data in client-expected camelCase fields. Raw pymysql bypasses SQL transformer."""
    import time as _time, pymysql, os
    ensure_db()
    tenant_id = request.args.get("tenantId") or request.user_id
    conn2 = pymysql.connect(
        host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"], charset="utf8mb4", ssl_disabled=True,
    )
    cur2 = conn2.cursor(pymysql.cursors.DictCursor)
    def q(sql, params=()):
        try:
            cur2.execute(sql, params)
            return cur2.fetchall()
        except Exception:
            return []
    user_row = None
    try:
        cur2.execute("SELECT * FROM user_style_profiles WHERE tenant_id=%s LIMIT 1", (tenant_id,))
        user_row = cur2.fetchone()
    except Exception:
        pass
    out = {
        "keywordRules":     q("SELECT * FROM keyword_rules WHERE tenant_id=%s", (tenant_id,)),
        "aiModelConfigs":   q("SELECT * FROM model_configs WHERE tenant_id=%s", (tenant_id,)),
        "messageBlacklist": q("SELECT * FROM message_blacklist WHERE tenant_id=%s", (tenant_id,)),
        "replyHistory":     q("SELECT * FROM reply_history WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 500", (tenant_id,)),
        "scenarios":        q("SELECT * FROM scenarios WHERE tenant_id=%s", (tenant_id,)),
    }
    conn2.close()
    return jsonify({"code": 0, "data": {
        **out,
        "userStyleProfile": dict(user_row) if user_row else None,
        "appConfigs": [],
        "serverTime": int(_time.time() * 1000),
    }})


@app.route("/sync/changes", methods=["GET"])
@require_auth
def sync_changes():
    """
    Incremental sync: return changes since a timestamp.
    Query: tenantId=<tenant_id>, since=<unix_ms>
    Response: full SyncChanges DTO with 11 fields
    """
    import pymysql, os, time
    ensure_db()
    tenant_id = request.args.get("tenantId") or request.user_id
    since = request.args.get("since", "0")
    conn2 = pymysql.connect(
        host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"], charset="utf8mb4", ssl_disabled=True,
    )
    cur2 = conn2.cursor(pymysql.cursors.DictCursor)
    try:
        cur2.execute(
            "SELECT * FROM keyword_rules WHERE tenant_id=%s AND updated_at > %s",
            (tenant_id, int(since) if str(since).isdigit() else 0),
        )
        rows = cur2.fetchall()
    except Exception:
        rows = []
    conn2.close()
    return jsonify({"code": 0, "data": {
        "keywordRules": rows, "aiModelConfigs": [], "userStyleProfile": None,
        "appConfigs": [], "scenarios": [], "replyHistory": [],
        "messageBlacklist": [], "deletedIds": {},
        "serverTime": int(time.time() * 1000), "hasMore": False, "nextCursor": None,
    }})


@app.route("/sync/push", methods=["POST"])
@require_auth
def sync_push():
    """
    Push client changes to server.
    Body: {rules: [...], models: [...]}
    Response: {applied: N}
    """
    ensure_db()
    tenant_id = request.user_id
    data = request.get_json(force=True, silent=True) or {}
    conn = get_connection()
    applied = 0
    try:
        for rule in data.get("rules", []):
            cols = list(rule.keys())
            vals = [rule[c] for c in cols]
            set_clause = ", ".join(f"{c}=?" for c in cols)
            conn.execute(
                f"INSERT OR REPLACE INTO keyword_rules ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                vals
            )
            applied += 1
        conn.commit()
        return jsonify({"applied": applied})
    finally:
        conn.close()


# ========== Backup routes (v1 legacy path compatibility) ==========

_in_memory_backups = {}  # {backup_id: {tenantId, data, created}}


@app.route("/api/v1/backup/upload", methods=["POST"])
@require_auth
def backup_upload_v1():
    """
    Upload a backup. Body: {deviceName, data, checksum, appVersion}
    Response: {backupId, createdAt}
    """
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict) or "data" not in data:
        return jsonify({"error": "Missing backup data"}), 400
    backup_id = secrets.token_hex(16)
    _in_memory_backups[backup_id] = {
        "tenantId": request.user_id,
        "data": data.get("data"),
        "deviceName": data.get("deviceName", "android"),
        "checksum": data.get("checksum", ""),
        "appVersion": data.get("appVersion", ""),
        "created": int(datetime.utcnow().timestamp() * 1000),
    }
    return jsonify({
        "backupId": backup_id,
        "createdAt": _in_memory_backups[backup_id]["created"],
    })


@app.route("/api/v1/backup/list", methods=["GET"])
@require_auth
def backup_list_v1():
    """List all backups for the current tenant."""
    tenant_id = request.user_id
    items = [
        {"backupId": bid, **{k: v for k, v in b.items() if k != "data"}}
        for bid, b in _in_memory_backups.items()
        if b.get("tenantId") == tenant_id
    ]
    return jsonify({"items": items})


@app.route("/api/v1/backup/download/<backup_id>", methods=["GET"])
@require_auth
def backup_download_v1(backup_id):
    """Download a specific backup by ID."""
    backup = _in_memory_backups.get(backup_id)
    if not backup or backup.get("tenantId") != request.user_id:
        return jsonify({"error": "Backup not found"}), 404
    return jsonify(backup.get("data", {}))


# ========== End Cloud Sync Compatibility Routes ==========

import time as _time
from datetime import datetime as _datetime

@app.route("/auth/refresh", methods=["POST"])
def auth_refresh():
    """
    Refresh access token via refreshToken.
    Request: {"refreshToken": "..."}
    Response: {userId, tenantId, token (new), refreshToken (new), expiresIn}
    """
    data = request.get_json(force=True, silent=True) or {}
    refresh_token = data.get("refreshToken")
    if not refresh_token:
        return jsonify({"error": "Missing refreshToken"}), 400
    try:
        import jwt as _jwt
        payload = _jwt.decode(refresh_token, JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("user_id")
        tenant_id = payload.get("tenant_id", user_id)
        if payload.get("type") != "refresh":
            return jsonify({"error": "Invalid token type"}), 401
    except _jwt.ExpiredSignatureError:
        return jsonify({"error": "Refresh token expired"}), 401
    except Exception as e:
        return jsonify({"error": f"Invalid refresh token: {e}"}), 401

    # Issue new tokens
    now = int(_time.time())
    access_payload = {
        "user_id": user_id, "tenant_id": tenant_id, "type": "access",
        "iat": now, "exp": now + 24 * 60 * 60,
    }
    new_access = _jwt.encode(access_payload, JWT_SECRET, algorithm="HS256")
    refresh_payload = {
        "user_id": user_id, "tenant_id": tenant_id, "type": "refresh",
        "iat": now, "exp": now + 30 * 24 * 60 * 60,
    }
    new_refresh = _jwt.encode(refresh_payload, JWT_SECRET, algorithm="HS256")

    return jsonify({
        "userId": user_id,
        "tenantId": tenant_id,
        "token": new_access,
        "refreshToken": new_refresh,
        "expiresIn": 30 * 24 * 60 * 60,
    })




@app.route("/sync/pull", methods=["GET"])
@require_auth
def sync_pull():
    """Incremental sync alias for WebView (Chrome UA) — delegates to /sync/all."""
    return sync_all()


@app.route("/sync/download", methods=["GET"])
@require_auth
def sync_download():
    """Alias for /sync/pull (WebView calls both)."""
    return sync_pull()


@app.route("/sync/resolve", methods=["POST"])
@require_auth
def sync_resolve():
    """Conflict stub."""
    return jsonify({"code": 0, "data": {"resolved": True, "serverTime": int(__import__("time").time() * 1000)}})


@app.route("/api/v1/backup/<int:backup_id>", methods=["DELETE"])
@require_auth
def backup_delete_v1(backup_id):
    """Delete a backup by ID (stub)."""
    return jsonify({"code": 0, "data": {"deleted": True}})

