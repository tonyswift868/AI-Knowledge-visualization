"""Normal background cancellation keeps its cost fence but permits later UI work."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile,threading
import pytest
from refactor.learning.connections.errors import ModelCancelled, ModelError
from .engine import Evidence,RecordedGateway


@pytest.mark.parametrize('cancelled',[True,False])
def test_simulation55_only_explicit_cancel_avoids_global_halt(tmp_path,cancelled):
    class Prices:
        def estimate_cost(self,*a,**k):return SimpleNamespace(high=Decimal('.23'),currency='CNY')
    class Real:
        price_catalog=Prices()
        def get_configuration(self):return dict(provider_id='deepseek',model='deepseek-v4-flash')
        def estimate_context(self):return {}
        def complete(self,*a,**k):raise ModelCancelled()
    root=Path(r'D:\MentorFlow\.local\test-tmp\small55\cancel-tests');root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as path:
        recorder=RecordedGateway(Real(),Evidence(path),budget='1')
        cancel=threading.Event()
        if cancelled:cancel.set()
        with pytest.raises(ModelCancelled):recorder.complete('point_materials',[],system='',cancel=cancel)
        assert recorder.halted.is_set() is not cancelled
        assert recorder.spent==Decimal('.23')
        assert 'time_cost_cny' not in recorder.evidence.calls[0]
        assert recorder.evidence.calls[0].get('expected_cancellation',False) is cancelled
        if cancelled:
            recorder.limit=Decimal('.24')
            with pytest.raises(ModelError,match='花费上限'):
                recorder.complete('point_materials',[],system='',cancel=cancel)
            assert recorder.halted.is_set()  # still cannot run beyond budget
