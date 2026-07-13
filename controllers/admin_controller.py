"""管理路由 (Flask Blueprint) — 一次性运维操作。

端点:
  POST /admin/migrate-keyword-index - 迁移 keyword_rules 唯一索引
    (删除 uk_tenant_keyword, 新建 uk_tenant_keyword_hash)

鉴权: 复用同步服务 Bearer token + 环境变量 ADMIN_USER_IDS 白名单校验。
"""
import os
import hashlib
import logging
from flask import Blueprint, request, jsonify, g
from functools import wraps
import jwt

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)


def _verify_token(token):
    JWT_SECRET = os.getenv('JWT_SECRET', 'default-secret-change-me')
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def _require_admin(f):
    """管理员鉴权: Bearer token + ADMIN_USER_IDS 白名单。

    ADMIN_USER_IDS 环境变量为逗号分隔的 user_id 列表, 未配置时拒绝所有访问。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'code': 401, 'message': '缺少认证令牌'}), 401
        payload = _verify_token(auth_header[7:])
        if not payload:
            return jsonify({'code': 401, 'message': '令牌无效或已过期'}), 401
        admin_ids = [uid.strip() for uid in os.getenv('ADMIN_USER_IDS', '').split(',') if uid.strip()]
        if not admin_ids:
            return jsonify({'code': 403, 'message': '未配置 ADMIN_USER_IDS, 禁止访问'}), 403
        if payload.get('user_id') not in admin_ids:
            return jsonify({'code': 403, 'message': '非管理员账号, 禁止访问'}), 403
        g.user_id = payload['user_id']
        g.tenant_id = payload.get('tenant_id') or payload.get('user_id', '')
        return f(*args, **kwargs)
    return decorated


def _calc_content_hash(rule):
    """与 sync_service.push_changes 一致的 content_hash 算法。
    计算 MD5(keyword+replyTemplate+category+targetType+targetNamesJson+priority+enabled+deleted)
    不含 id, 使跨设备相同内容也能被去重。
    """
    import hashlib
    raw = (str(rule.get('keyword', '')) + str(rule.get('replyTemplate', ''))
           + str(rule.get('category', '')) + str(rule.get('targetType', ''))
           + str(rule.get('targetNamesJson', '')) + str(rule.get('priority', 0))
           + str(rule.get('enabled', True)) + str(rule.get('deleted', False)))
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def _calc_keyword_hash(keyword, reply):
    """计算 keyword + reply 的 MD5 哈希。

    用于索引迁移时生成 keyword_hash 字段。
    None 值视为空字符串。
    """
    import hashlib
    raw = (str(keyword or '') + str(reply or ''))
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


@admin_bp.route('/admin/migrate-content-hash', methods=['POST'])
@_require_admin
def migrate_content_hash():
    """迁移 keyword_rules 唯一索引: keyword_hash → content_hash。

    步骤:
      1. 添加 content_hash 列
      2. 回填存量数据的 content_hash (MD5(id+keyword+replyTemplate+...))
      3. 清理同 (tenant_id, content_hash) 重复行, 保留 MIN(id)
      4. 删除旧唯一索引 (uk_tenant_keyword, uk_tenant_keyword_hash)
      5. 新建唯一索引 uk_tenant_content_hash (tenant_id, content_hash)

    幂等: 重复调用不会报错, 已存在的列/索引会被跳过。
    """
    try:
        from config.database import execute_query, execute_update
        results = []

        # Step 1: 添加 content_hash 列
        try:
            execute_update(
                "ALTER TABLE keyword_rules ADD COLUMN content_hash VARCHAR(64)"
            )
            results.append('step1_add_column: added content_hash column')
        except Exception as e:
            results.append(f'step1_add_column: skipped (column may exist) - {e}')

        # Step 2: 回填存量数据的 content_hash (不含 id, 跨设备去重)
        backfill_count = execute_update(
            """UPDATE keyword_rules
               SET content_hash = MD5(CONCAT(COALESCE(keyword,''),
                   COALESCE(reply_template,''), COALESCE(category,''),
                   COALESCE(target_type,''), COALESCE(target_names_json,''),
                   COALESCE(CAST(priority AS CHAR),'0'),
                   COALESCE(CAST(enabled AS CHAR),'1'),
                   COALESCE(CAST(deleted AS CHAR),'0')))
               WHERE content_hash IS NULL OR content_hash = ''"""
        )
        results.append(f'step2_backfill: updated {backfill_count} rows')

        # Step 3: 清理同 (tenant_id, content_hash) 重复行, 保留 MIN(id)
        dup_check = execute_query(
            """SELECT COUNT(*) FROM (
                SELECT tenant_id, content_hash, COUNT(*) c
                FROM keyword_rules
                WHERE content_hash IS NOT NULL AND content_hash != ''
                GROUP BY tenant_id, content_hash HAVING c > 1
            ) t"""
        )
        dup_groups = dup_check[0][0] if dup_check else 0
        if dup_groups > 0:
            deleted_count = execute_update(
                """DELETE FROM keyword_rules
                   WHERE id NOT IN (
                       SELECT MIN(id) FROM keyword_rules
                       WHERE content_hash IS NOT NULL AND content_hash != ''
                       GROUP BY tenant_id, content_hash
                   )
                   AND content_hash IS NOT NULL AND content_hash != ''"""
            )
            results.append(f'step3_dedup: found {dup_groups} dup groups, deleted {deleted_count} rows')
        else:
            results.append('step3_dedup: no duplicates found')

        # Step 4: 删除旧唯一索引
        for idx in ['uk_tenant_keyword', 'uk_tenant_keyword_hash']:
            try:
                execute_update(f"DROP INDEX {idx} ON keyword_rules")
                results.append(f'step4_drop_index: dropped {idx}')
            except Exception as e:
                results.append(f'step4_drop_index: skipped {idx} - {e}')

        # Step 5: 新建唯一索引 uk_tenant_content_hash
        try:
            execute_update(
                """CREATE UNIQUE INDEX uk_tenant_content_hash
                   ON keyword_rules(tenant_id, content_hash)"""
            )
            results.append('step5_create_index: created uk_tenant_content_hash')
        except Exception as e:
            results.append(f'step5_create_index: skipped (may exist) - {e}')

        logger.info(f"migrate_content_hash completed: {results}")
        return jsonify({
            'code': 0,
            'message': 'content_hash 索引迁移完成',
            'data': {'steps': results, 'dupGroups': dup_groups}
        })
    except Exception as e:
        logger.error(f"migrate_content_hash failed: {e}", exc_info=True)
        return jsonify({'code': 500, 'message': str(e)}), 500


@admin_bp.route('/admin/content-hash-status', methods=['GET'])
@_require_admin
def content_hash_status():
    """查询 keyword_rules content_hash 索引状态。"""
    try:
        from config.database import execute_query

        indexes = execute_query("SHOW INDEX FROM keyword_rules")
        index_info = [
            {'name': r[2], 'column': r[4], 'non_unique': r[1]}
            for r in (indexes or [])
        ]

        total = execute_query(
            "SELECT COUNT(*) FROM keyword_rules WHERE deleted=FALSE"
        )
        total_count = total[0][0] if total else 0

        null_hash = execute_query(
            "SELECT COUNT(*) FROM keyword_rules WHERE content_hash IS NULL OR content_hash=''"
        )
        null_hash_count = null_hash[0][0] if null_hash else 0

        dup_groups = execute_query(
            """SELECT COUNT(*) FROM (
                SELECT tenant_id, content_hash, COUNT(*) c
                FROM keyword_rules
                WHERE content_hash IS NOT NULL AND content_hash != '' AND deleted=FALSE
                GROUP BY tenant_id, content_hash HAVING c > 1
            ) t"""
        )
        dup_count = dup_groups[0][0] if dup_groups else 0

        return jsonify({
            'code': 0,
            'data': {
                'indexes': index_info,
                'totalActiveRules': total_count,
                'nullHashRows': null_hash_count,
                'dupGroups': dup_count
            }
        })
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500
