"""Explicit offline checks; no live credentials or network calls."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deps',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.deps))
    from run420 import PaidAPI, body_rejection, canonical, prefilter
    assert canonical('https://person:secret@example.org/a') is None
    assert canonical('file:///C:/private') is None
    assert prefilter(dict(url='https://example.org/login',title='Login'))
    assert body_rejection('Verify you are human ' * 20,'Challenge') == 'challenge'
    assert body_rejection('tiny','Lesson') == 'short_body'
    assert body_rejection('This lesson explains a method with worked examples. '*10,'Tutorial') == ''

    class Reply:
        status_code=200
        def json(self):
            return {'usage':{'prompt_tokens':100,'completion_tokens':200,
                'completion_tokens_details':{'reasoning_tokens':150}}}
    class OfflineHTTP:
        def __init__(self):self.calls=0
        def post(self,*_args,**_kwargs):self.calls+=1;return Reply()
    # Avoid the constructor's real HTTP client entirely.
    import threading
    api=object.__new__(PaidAPI)
    api.http=OfflineHTTP();api.model='offline';api.work=args.work
    api.budget=0.001;api.records=[];api.reserved=0;api.lock=threading.Lock()
    try:api.request('model',{},'offline',0.025)
    except RuntimeError as exc:assert str(exc)=='BUDGET_LIMIT'
    else:raise AssertionError('budget gate did not reject')
    assert api.http.calls==0
    api.budget=1
    api.request('model',{},'offline',0.025)
    assert api.http.calls==1
    # Completion tokens already include reasoning; do not add those twice.
    assert abs(api.cost()-(100*2+200*8)/1e6)<1e-12
    assert api.reserved==0
    # Refuse to reset a prior paid ledger before loading any key or dependency.
    from contextlib import redirect_stderr
    from io import StringIO
    from unittest.mock import patch
    from run420 import main as live_main
    with patch.object(sys,'argv',['run420.py','--work',str(args.work),'--deps',str(args.deps)]):
        with redirect_stderr(StringIO()):
            try:live_main()
            except SystemExit as exc:assert exc.code==2
            else:raise AssertionError('existing ledger was overwritten')
    assert api.http.calls==1
    print('check_runner420 offline checks passed')


if __name__=='__main__':main()
