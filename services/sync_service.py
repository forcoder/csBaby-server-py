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
        deleted_ids = {}
        for entity_name, table in self.ENTITY_TABLES.items():
            result = execute_query(
                f"SELECT id FROM {table} WHERE tenant_id=%s AND sync_version>%s AND deleted=TRUE",
                (tenant_id, since)
            )
            if result:
                deleted_ids[entity_name] = [str(r[0]) for r in result]

        keyword_rules = execute_query(
            "SELECT * FROM keyword_rules WHERE tenant_id=%s AND sync_version>%s LIMIT %s OFFSET %s",
            (tenant_id, since, limit, (page-1)*limit)
        )
        return {
            'keywordRules': [to_rule(r) for r in keyword_rules],
            'deletedIds': deleted_ids,
            'serverTime': now, 'page': page, 'limit': limit,
            'hasMore': len(keyword_rules) >= limit
        }

    def push_changes(self, tenant_id, data):
        now = int(datetime.now().timestamp() * 1000)
        stats = {'inserted': 0}
        for r in data.get('keywordRules', []):
            execute_update(
                """INSERT INTO keyword_rules (id, keyword, match_type, reply_template, category,
                    target_type, target_names_json, priority, enabled, created_at, updated_at,
                    tenant_id, sync_version, deleted)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   keyword=EXCLUDED.keyword, sync_version=EXCLUDED.sync_version""",
                (r['id'], r.get('keyword'), r.get('matchType'), r.get('replyTemplate'),
                 r.get('category'), r.get('targetType'), r.get('targetNamesJson'),
                 r.get('priority', 0), 1 if r.get('enabled') else 0,
                 r.get('createdAt', now), r.get('updatedAt', now),
                 tenant_id, now, 1 if r.get('deleted') else 0)
            )
            stats['inserted'] += 1
        update_checkpoint(tenant_id, now)
        return {'accepted': True, 'conflicts': [], 'newServerVersion': now, 'serverTime': now, 'stats': stats}