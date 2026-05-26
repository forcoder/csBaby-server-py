import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.database import execute_query, execute_update
from models.sync_checkpoint import update_checkpoint
from datetime import datetime

def to_rule(r):
    return {'id': r[0], 'keyword': r[1], 'matchType': r[2], 'replyTemplate': r[3],
            'category': r[4], 'targetType': r[5], 'targetNamesJson': r[6],
            'priority': r[7], 'enabled': bool(r[8]), 'createdAt': r[9], 'updatedAt': r[10],
            'tenantId': r[11], 'syncVersion': r[12], 'deleted': bool(r[13])}

def to_model(m):
    return {'id': m[0], 'modelType': m[1], 'modelName': m[2], 'apiKey': m[3],
            'apiEndpoint': m[4], 'temperature': m[5], 'maxTokens': m[6],
            'isDefault': bool(m[7]), 'isEnabled': bool(m[8]), 'monthlyCost': m[9],
            'lastUsed': m[10], 'createdAt': m[11], 'tenantId': m[12],
            'syncVersion': m[13], 'deleted': bool(m[14])}

def to_profile(p):
    if not p: return None
    return {'userId': p[0], 'formalityLevel': p[1], 'enthusiasmLevel': p[2],
            'professionalismLevel': p[3], 'wordCountPreference': p[4],
            'commonPhrases': p[5], 'avoidPhrases': p[6], 'learningSamples': p[7],
            'accuracyScore': p[8], 'lastTrained': p[9], 'createdAt': p[10],
            'tenantId': p[11], 'syncVersion': p[12], 'deleted': bool(p[13])}

def to_app(a):
    return {'packageName': a[0], 'appName': a[1], 'iconUri': a[2],
            'isMonitored': bool(a[3]), 'createdAt': a[4], 'lastUsed': a[5],
            'tenantId': a[6], 'syncVersion': a[7], 'deleted': bool(a[8])}

def to_scenario(s):
    return {'id': s[0], 'name': s[1], 'type': s[2], 'targetId': s[3],
            'description': s[4], 'createdAt': s[5], 'tenantId': s[6],
            'syncVersion': s[7], 'deleted': bool(s[8])}

def to_reply(h):
    return {'id': h[0], 'sourceApp': h[1], 'originalMessage': h[2],
            'generatedReply': h[3], 'finalReply': h[4], 'ruleMatchedId': h[5],
            'modelUsedId': h[6], 'styleApplied': bool(h[7]), 'sendTime': h[8],
            'modified': bool(h[9]), 'tenantId': h[10], 'syncVersion': h[11], 'deleted': bool(h[12])}

def to_blacklist(b):
    return {'id': b[0], 'type': b[1], 'value': b[2], 'description': b[3],
            'packageName': b[4], 'createdAt': b[5], 'isEnabled': bool(b[6]),
            'tenantId': b[7], 'syncVersion': b[8], 'deleted': bool(b[9])}

class SyncService:
    ENTITY_TABLES = {
        'keyword_rules': 'keyword_rules', 'ai_model_configs': 'ai_model_configs',
        'user_style_profiles': 'user_style_profiles', 'app_configs': 'app_configs',
        'scenarios': 'scenarios', 'reply_history': 'reply_history',
        'message_blacklist': 'message_blacklist'
    }

    def full_sync(self, tenant_id):
        now = int(datetime.now().timestamp() * 1000)
        keyword_rules = execute_query(
            "SELECT * FROM keyword_rules WHERE tenant_id=%s AND deleted=FALSE ORDER BY priority DESC",
            (tenant_id,)
        )
        ai_models = execute_query(
            "SELECT * FROM ai_model_configs WHERE tenant_id=%s AND deleted=FALSE", (tenant_id,)
        )
        profile = execute_query(
            "SELECT * FROM user_style_profiles WHERE tenant_id=%s AND deleted=FALSE",
            (tenant_id,), fetch='one'
        )
        apps = execute_query(
            "SELECT * FROM app_configs WHERE tenant_id=%s AND deleted=FALSE", (tenant_id,)
        )
        scenarios = execute_query(
            "SELECT * FROM scenarios WHERE tenant_id=%s AND deleted=FALSE", (tenant_id,)
        )
        replies = execute_query(
            "SELECT * FROM reply_history WHERE tenant_id=%s AND deleted=FALSE LIMIT 500", (tenant_id,)
        )
        blacklist = execute_query(
            "SELECT * FROM message_blacklist WHERE tenant_id=%s AND deleted=FALSE", (tenant_id,)
        )
        return {
            'keywordRules': [to_rule(r) for r in keyword_rules],
            'aiModelConfigs': [to_model(m) for m in ai_models],
            'userStyleProfile': to_profile(profile),
            'appConfigs': [to_app(a) for a in apps],
            'scenarios': [to_scenario(s) for s in scenarios],
            'replyHistory': [to_reply(h) for h in replies],
            'messageBlacklist': [to_blacklist(b) for b in blacklist],
            'serverTime': now
        }

    def incremental_sync(self, tenant_id, since, page=1, limit=100):
        now = int(datetime.now().timestamp() * 1000)
        offset = (page - 1) * limit
        deleted_ids = {}
        for entity_name, table in self.ENTITY_TABLES.items():
            id_col = 'package_name' if table == 'app_configs' else 'id'
            result = execute_query(
                f"SELECT {id_col} FROM {table} WHERE tenant_id=%s AND sync_version>%s AND deleted=TRUE",
                (tenant_id, since)
            )
            if result:
                deleted_ids[entity_name] = [str(r[0]) for r in result]

        keyword_rules = execute_query(
            "SELECT * FROM keyword_rules WHERE tenant_id=%s AND sync_version>%s LIMIT %s OFFSET %s",
            (tenant_id, since, limit, offset)
        )
        ai_models = execute_query(
            "SELECT * FROM ai_model_configs WHERE tenant_id=%s AND sync_version>%s LIMIT %s OFFSET %s",
            (tenant_id, since, limit, offset)
        )
        profile = execute_query(
            "SELECT * FROM user_style_profiles WHERE tenant_id=%s AND sync_version>%s",
            (tenant_id, since), fetch='one'
        )
        apps = execute_query(
            "SELECT * FROM app_configs WHERE tenant_id=%s AND sync_version>%s LIMIT %s OFFSET %s",
            (tenant_id, since, limit, offset)
        )
        scenarios = execute_query(
            "SELECT * FROM scenarios WHERE tenant_id=%s AND sync_version>%s LIMIT %s OFFSET %s",
            (tenant_id, since, limit, offset)
        )
        replies = execute_query(
            "SELECT * FROM reply_history WHERE tenant_id=%s AND sync_version>%s LIMIT %s OFFSET %s",
            (tenant_id, since, limit, offset)
        )
        blacklist = execute_query(
            "SELECT * FROM message_blacklist WHERE tenant_id=%s AND sync_version>%s LIMIT %s OFFSET %s",
            (tenant_id, since, limit, offset)
        )
        total_changes = len(keyword_rules) + len(ai_models) + len(apps) + len(scenarios) + len(replies) + len(blacklist)
        if profile:
            total_changes += 1
        return {
            'keywordRules': [to_rule(r) for r in keyword_rules],
            'aiModelConfigs': [to_model(m) for m in ai_models],
            'userStyleProfile': to_profile(profile),
            'appConfigs': [to_app(a) for a in apps],
            'scenarios': [to_scenario(s) for s in scenarios],
            'replyHistory': [to_reply(h) for h in replies],
            'messageBlacklist': [to_blacklist(b) for b in blacklist],
            'deletedIds': deleted_ids,
            'serverTime': now, 'page': page, 'limit': limit,
            'hasMore': total_changes >= limit
        }

    def push_changes(self, tenant_id, data):
        now = int(datetime.now().timestamp() * 100)
        stats = {'inserted': 0, 'updated': 0, 'deleted': 0}

        # 处理 keyword_rules
        for r in data.get('keywordRules', []):
            execute_update(
                """INSERT INTO keyword_rules (id, keyword, match_type, reply_template, category,
                    target_type, target_names_json, priority, enabled, created_at, updated_at,
                    tenant_id, sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   keyword=EXCLUDED.keyword, match_type=EXCLUDED.match_type,
                   reply_template=EXCLUDED.reply_template, category=EXCLUDED.category,
                   target_type=EXCLUDED.target_type, target_names_json=EXCLUDED.target_names_json,
                   priority=EXCLUDED.priority, enabled=EXCLUDED.enabled,
                   updated_at=EXCLUDED.updated_at, sync_version=EXCLUDED.sync_version,
                   deleted=EXCLUDED.deleted""",
                (str(r.get('id', '')), r.get('keyword', ''), r.get('matchType', ''),
                 r.get('replyTemplate', ''), r.get('category', ''),
                 r.get('targetType', 'ALL'), r.get('targetNamesJson', '[]'),
                 r.get('priority', 0), r.get('enabled', True),
                 r.get('createdAt', now), r.get('updatedAt', now),
                 tenant_id, now, r.get('deleted', False))
            )
            stats['inserted'] += 1

        # 处理 ai_model_configs
        for m in data.get('aiModelConfigs', []):
            execute_update(
                """INSERT INTO ai_model_configs (id, model_type, model_name, api_key, api_endpoint,
                    temperature, max_tokens, is_default, is_enabled, monthly_cost, last_used,
                    created_at, tenant_id, sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   model_type=EXCLUDED.model_type, model_name=EXCLUDED.model_name,
                   api_key=EXCLUDED.api_key, api_endpoint=EXCLUDED.api_endpoint,
                   temperature=EXCLUDED.temperature, max_tokens=EXCLUDED.max_tokens,
                   is_default=EXCLUDED.is_default, is_enabled=EXCLUDED.is_enabled,
                   monthly_cost=EXCLUDED.monthly_cost, last_used=EXCLUDED.last_used,
                   sync_version=EXCLUDED.sync_version, deleted=EXCLUDED.deleted""",
                (str(m.get('id', '')), m.get('modelType', ''), m.get('modelName', ''),
                 m.get('apiKey', ''), m.get('apiEndpoint', ''),
                 m.get('temperature', 0.7), m.get('maxTokens', 1000),
                 m.get('isDefault', False), m.get('isEnabled', True),
                 m.get('monthlyCost', 0), m.get('lastUsed', 0),
                 m.get('createdAt', now), tenant_id, now, m.get('deleted', False))
            )
            stats['inserted'] += 1

        # 处理 user_style_profiles (upsert by user_id)
        profile = data.get('userStyleProfile')
        if profile:
            execute_update(
                """INSERT INTO user_style_profiles (id, user_id, formality_level, enthusiasm_level,
                    professionalism_level, word_count_preference, common_phrases, avoid_phrases,
                    learning_samples, accuracy_score, last_trained, created_at, tenant_id,
                    sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id) DO UPDATE SET
                   formality_level=EXCLUDED.formality_level, enthusiasm_level=EXCLUDED.enthusiasm_level,
                   professionalism_level=EXCLUDED.professionalism_level,
                   word_count_preference=EXCLUDED.word_count_preference,
                   common_phrases=EXCLUDED.common_phrases, avoid_phrases=EXCLUDED.avoid_phrases,
                   learning_samples=EXCLUDED.learning_samples, accuracy_score=EXCLUDED.accuracy_score,
                   last_trained=EXCLUDED.last_trained, sync_version=EXCLUDED.sync_version,
                   deleted=EXCLUDED.deleted""",
                (str(profile.get('userId', '')), profile.get('userId', ''),
                 profile.get('formalityLevel', 0.5), profile.get('enthusiasmLevel', 0.5),
                 profile.get('professionalismLevel', 0.5), profile.get('wordCountPreference', 50),
                 profile.get('commonPhrases', '[]'), profile.get('avoidPhrases', '[]'),
                 profile.get('learningSamples', 0), profile.get('accuracyScore', 0),
                 profile.get('lastTrained', 0), profile.get('createdAt', now),
                 tenant_id, now, profile.get('deleted', False))
            )
            stats['inserted'] += 1

        # 处理 app_configs (upsert by package_name)
        for a in data.get('appConfigs', []):
            execute_update(
                """INSERT INTO app_configs (package_name, app_name, icon_uri, is_monitored,
                    created_at, last_used, tenant_id, sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (package_name) DO UPDATE SET
                   app_name=EXCLUDED.app_name, icon_uri=EXCLUDED.icon_uri,
                   is_monitored=EXCLUDED.is_monitored, last_used=EXCLUDED.last_used,
                   sync_version=EXCLUDED.sync_version, deleted=EXCLUDED.deleted""",
                (a.get('packageName', ''), a.get('appName', ''), a.get('iconUri'),
                 a.get('isMonitored', False), a.get('createdAt', now),
                 a.get('lastUsed', 0), tenant_id, now, a.get('deleted', False))
            )
            stats['inserted'] += 1

        # 处理 scenarios
        for s in data.get('scenarios', []):
            execute_update(
                """INSERT INTO scenarios (id, name, type, target_id, description, created_at,
                    tenant_id, sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   name=EXCLUDED.name, type=EXCLUDED.type, target_id=EXCLUDED.target_id,
                   description=EXCLUDED.description, sync_version=EXCLUDED.sync_version,
                   deleted=EXCLUDED.deleted""",
                (str(s.get('id', '')), s.get('name', ''), s.get('type', ''),
                 s.get('targetId'), s.get('description'),
                 s.get('createdAt', now), tenant_id, now, s.get('deleted', False))
            )
            stats['inserted'] += 1

        # 处理 reply_history
        for h in data.get('replyHistory', []):
            execute_update(
                """INSERT INTO reply_history (id, source_app, original_message, generated_reply,
                    final_reply, rule_matched_id, model_used_id, style_applied, send_time,
                    modified, tenant_id, sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   source_app=EXCLUDED.source_app, original_message=EXCLUDED.original_message,
                   generated_reply=EXCLUDED.generated_reply, final_reply=EXCLUDED.final_reply,
                   rule_matched_id=EXCLUDED.rule_matched_id, model_used_id=EXCLUDED.model_used_id,
                   style_applied=EXCLUDED.style_applied, send_time=EXCLUDED.send_time,
                   modified=EXCLUDED.modified, sync_version=EXCLUDED.sync_version,
                   deleted=EXCLUDED.deleted""",
                (str(h.get('id', '')), h.get('sourceApp', ''), h.get('originalMessage', ''),
                 h.get('generatedReply', ''), h.get('finalReply', ''),
                 h.get('ruleMatchedId'), h.get('modelUsedId'),
                 h.get('styleApplied', False), h.get('sendTime', 0),
                 h.get('modified', False), tenant_id, now, h.get('deleted', False))
            )
            stats['inserted'] += 1

        # 处理 message_blacklist
        for b in data.get('messageBlacklist', []):
            execute_update(
                """INSERT INTO message_blacklist (id, type, value, description, package_name,
                    created_at, is_enabled, tenant_id, sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   type=EXCLUDED.type, value=EXCLUDED.value, description=EXCLUDED.description,
                   package_name=EXCLUDED.package_name, is_enabled=EXCLUDED.is_enabled,
                   sync_version=EXCLUDED.sync_version, deleted=EXCLUDED.deleted""",
                (str(b.get('id', '')), b.get('type', ''), b.get('value', ''),
                 b.get('description', ''), b.get('packageName'),
                 b.get('createdAt', now), b.get('isEnabled', True),
                 tenant_id, now, b.get('deleted', False))
            )
            stats['inserted'] += 1

        # 处理删除
        for entity_type, ids in data.get('deletedIds', {}).items():
            table = self.ENTITY_TABLES.get(entity_type)
            if not table:
                continue
            id_col = 'package_name' if table == 'app_configs' else 'id'
            for item_id in ids:
                execute_update(
                    f"UPDATE {table} SET deleted=TRUE, sync_version=%s WHERE tenant_id=%s AND {id_col}=%s",
                    (now, tenant_id, str(item_id))
                )
                stats['deleted'] += 1

        update_checkpoint(tenant_id, now)
        return {'accepted': True, 'conflicts': [], 'newServerVersion': now, 'serverTime': now, 'stats': stats}