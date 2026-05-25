import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers.health_controller import HealthCheck

def test_health_controller():
    from controllers.health_controller import HealthCheck
    assert HealthCheck is not None
    assert hasattr(HealthCheck, 'GET')