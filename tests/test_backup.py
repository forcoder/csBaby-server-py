import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.backup_service import BackupService

def test_backup_service():
    service = BackupService()
    assert hasattr(service, 'upload_backup')
    assert hasattr(service, 'get_backup_list')
    assert hasattr(service, 'download_backup')
    assert hasattr(service, 'restore_backup')
    assert hasattr(service, 'delete_backup')