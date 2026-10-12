"""106: the cloud-read banner follows actual pull/upload receipts."""
from threading import Event
from types import SimpleNamespace
from refactor.learning.app.sync import _WindowCourseSyncService
from refactor.runtime_logging import DiagnosticFormatter
import logging


def test_sync106_no_empty_pull_banner_then_actual_upload_and_pull():
    published=[]
    class Signal:
        def emit(self, value): published.append(value)
    round_id=object();cancel=Event()
    fake=SimpleNamespace(_closed=False,_active=True,_cancel=cancel,_progress_round=round_id,
        _finished_progress_round=None,_status={'at':None},status_changed=Signal(),state_changed=Signal())
    fake.status=lambda:dict(fake._status)
    def show(phase,completed,total=0):
        _WindowCourseSyncService._show_progress(fake,dict(phase=phase,completed=completed,total=total,
            failed=0,round=round_id,base=0),cancel)
        return fake._status
    assert not show('start',0)['show_progress']
    assert not show('pull',0)['show_progress']
    assert show('pull',500)['show_progress'] and '500' in fake._status['message']
    assert show('upload',100,479)['show_progress']
    assert '100 / 共 479' in fake._status['message'] and '读取' not in fake._status['message']
    assert not show('upload',0,0)['show_progress']


def test_sync106_log_formatter_retains_counts_without_record_contents():
    record=logging.LogRecord('refactor.runtime',logging.INFO,__file__,1,'not serialized',(),None)
    record.mf_event='sync'
    record.mf_fields=dict(state='UPLOAD_BATCH_END',count=100,completed=200,total=479,
                          elapsed_seconds=1.25,token='sensitive',payload='student text')
    import json
    formatted=DiagnosticFormatter().format(record)
    row=json.loads(formatted)
    assert row['count']==100 and row['completed']==200 and row['total']==479
    assert row['elapsed_seconds']==1.25
    assert 'sensitive' not in formatted and 'student text' not in formatted
