import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.sync_service import SyncService, to_rule, to_model, to_profile

def test_entity_transformers():
    rule_tuple = ('123', 'test', 'CONTAINS', 'Reply', 'cat', 'ALL', '[]', 0, True, 1000, 2000, 't1', 3000, False)
    rule = to_rule(rule_tuple)
    assert rule['id'] == '123'
    assert rule['keyword'] == 'test'
    assert rule['enabled'] == True
    assert rule['deleted'] == False

    model_tuple = ('456', 'openai', 'GPT-4', 'key', 'url', 0.7, 1000, True, True, 100, 2000, 3000, 't1', 4000, False)
    model = to_model(model_tuple)
    assert model['id'] == '456'
    assert model['isDefault'] == True

    profile_tuple = ('u1', 0.5, 0.6, 0.7, 50, '[]', '[]', '[]', 0.8, 1000, 2000, 't1', 3000, False)
    profile = to_profile(profile_tuple)
    assert profile['userId'] == 'u1'
    assert profile['formalityLevel'] == 0.5

def test_sync_service():
    service = SyncService()
    assert hasattr(service, 'full_sync')
    assert hasattr(service, 'incremental_sync')
    assert hasattr(service, 'push_changes')