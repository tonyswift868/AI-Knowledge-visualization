"""Adapt existing browser-fixture ingesters to the new asynchronous bridge.

The old fixtures still own their deterministic gates, counters and failures;
this adapter changes only the protocol around those operations. Product queue
and lifecycle behavior is tested against RagService separately in rag263.
"""
from dataclasses import replace
from types import SimpleNamespace
import threading
from uuid import uuid4


def register_legacy_preparation(bridge, documents, ingest=None):
    lock = threading.RLock()
    scope = [uuid4().hex]
    jobs = {}
    owners = {}
    context = threading.local()
    original_push = bridge.push

    def snapshot(_params=None, _ctx=None):
        ids = (_params or {}).get('document_ids')
        with lock:
            rows = []
            for job in jobs.values():
                if ids is not None and job['document_id'] not in ids:
                    continue
                row = {key: value for key, value in job.items() if key != 'cancel'}
                doc = next((d for d in documents if d['id'] == job['document_id']), {})
                row.update(uploaded_chunks=doc.get('uploaded_chunks', 0), total_chunks=doc.get('total_chunks', 0))
                rows.append(row)
            return {'scope_id': scope[0], 'jobs': rows}

    def push(name, data):
        if name == 'scope_changed':
            with lock:
                for job in jobs.values():
                    job['cancel'].set()
                jobs.clear()
                owners.clear()
                scope[0] = uuid4().hex
        if name == 'rag.progress':
            data = dict(data)
            document_id = data.get('id') or data.get('document_id')
            with lock:
                worker_scope = getattr(context, 'scope', scope[0])
                if worker_scope != scope[0]:
                    return
                job = getattr(context, 'jobs', {}).get(document_id)
                if job is None and getattr(context, 'send_cancel', None) is not None:
                    job = {'id': uuid4().hex, 'document_id': document_id, 'state': 'running',
                           'cancel': context.send_cancel, 'error': '', 'sequence': len(jobs) + 1}
                    jobs[job['id']] = owners[document_id] = context.jobs[document_id] = job
                if job:
                    job['state'] = {'ready': 'ready', 'failed': 'failed'}.get(data.get('status'), 'running')
                    job['error'] = data.get('error') or ''
                    data.update(job_id=job['id'], scope_id=scope[0], preparation_state=job['state'], sequence=job['sequence'])
        original_push(name, data)

    bridge.push = push

    def prepare(params, _ctx):
        selected = list(dict.fromkeys(params.get('document_ids', [])))
        active = {}
        cancel = threading.Event()
        with lock:
            worker_scope = scope[0]
            for document_id in selected:
                doc = next(d for d in documents if d['id'] == document_id)
                previous = owners.get(document_id)
                if previous and (previous['state'] in {'queued', 'running'}
                                 or previous['state'] == 'ready' and doc['status'] == 'ready'):
                    continue
                job = {'id': uuid4().hex, 'document_id': document_id,
                       'state': 'ready' if doc['status'] == 'ready' else 'queued',
                       'cancel': cancel, 'error': '', 'sequence': len(jobs) + 1}
                jobs[job['id']] = owners[document_id] = job
                if job['state'] != 'ready':
                    active[document_id] = job

        def work():
            context.scope, context.jobs = worker_scope, active
            try:
                if ingest is None:
                    raise AssertionError('fixture did not provide an ingester')
                ingest({'document_ids': list(active)}, SimpleNamespace(cancel=cancel))
            except Exception as error:
                with lock:
                    for job in active.values():
                        job['state'] = 'cancelled' if cancel.is_set() else 'failed'
                        job['error'] = str(error)
            finally:
                with lock:
                    for document_id, job in active.items():
                        doc = next(d for d in documents if d['id'] == document_id)
                        job['state'] = 'cancelled' if cancel.is_set() else 'ready' if doc['status'] == 'ready' else 'failed'
                        job['error'] = doc.get('error') or job['error']

        if active:
            threading.Thread(target=work, daemon=True).start()
        return snapshot({'document_ids': selected})

    def cancel(params, _ctx):
        with lock:
            for job_id in params.get('job_ids', []):
                if job_id in jobs:
                    job = jobs[job_id]
                    job['cancel'].set()
                    job['state'] = 'cancelled'
        return snapshot()

    bridge.register('rag.prepare', prepare)
    bridge.register('rag.preparation', snapshot)
    bridge.register('rag.cancel_preparation', cancel)
    if 'rag.send' in bridge._handlers:
        handler = bridge._handlers['rag.send']

        def send(params, ctx):
            context.scope, context.jobs, context.send_cancel = scope[0], {}, ctx.cancel
            try:
                return handler.function(params, ctx)
            finally:
                context.send_cancel = None

        bridge._handlers['rag.send'] = replace(handler, function=send)
    bridge.closing.connect(lambda: cancel({'job_ids': list(jobs)}, None))
