"""Small failure-only diagnostic snapshot; never records headers or payloads."""
from collections import deque
import json
import re
from urllib.parse import quote


class FailureEvidence:
    def __init__(self, provider, key):
        self.provider = provider
        self._private = {key, quote(key, safe=''), json.dumps(key, ensure_ascii=False)[1:-1]} - {''}
        self.status = None
        self.content_type = None
        self.vendor_type = None
        self.vendor_message = None
        self.events = deque(maxlen=8)
        self.stage = 'before_response'

    def request(self, body):
        def collect(value, depth=0):
            if depth > 12:
                return
            if isinstance(value, str):
                self._private.add(value)
                self._private.update(part.strip() for part in value.splitlines() if part.strip())
                # Labelled dialogue JSON contains the original student string,
                # distinct from its escaped on-wire representation.
                for part in value.splitlines():
                    try:
                        nested = json.loads(part)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(nested, (dict, list)):
                        collect(nested, depth+1)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item, depth+1)
            elif isinstance(value, list):
                for item in value:
                    collect(item, depth+1)
        collect(body.get('messages', []))
        collect(body.get('system', ''))

    def clean(self, value):
        if not isinstance(value, str):
            return None
        text = value
        for private in sorted(self._private, key=len, reverse=True):
            if private and len(private) <= len(text):
                for variant in {private, json.dumps(private, ensure_ascii=False)[1:-1], quote(private, safe='')}:
                    text = text.replace(variant, '[redacted]')
        # Do not retain snippets from an echoed request, or quoted free text.
        # Other structured error fields/status still explain protocol failure.
        if re.search(r'\b(prompt|content|input|messages?)\b|输入|回答|学生|请求正文', text, re.I):
            return '[request-related message omitted]'
        text = re.sub(r'(?i)\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}', '[redacted]', text)
        text = re.sub(r'(?i)Bearer\s+[^\s,;]+', 'Bearer [redacted]', text)
        text = re.sub(r'"[^"\n]*"|“[^”\n]*”', '[quoted text omitted]', text)
        return ''.join(c if c >= ' ' else ' ' for c in text)[:512]

    def event(self, name, data):
        kind = data.get('type', name) if isinstance(data, dict) else name
        kind = self.clean(kind)
        self.events.append(kind if kind and re.fullmatch(r'[\w.:-]{1,80}', kind, flags=re.ASCII) else 'unrecognized_event')
        self.error(data)

    def error(self, data):
        if not isinstance(data, dict):
            return
        error = data.get('error')
        if isinstance(error, str):
            self.vendor_message = self.clean(error)
        elif isinstance(error, dict):
            self.vendor_type = self.clean(error.get('type') or error.get('code') or error.get('status'))
            self.vendor_message = self.clean(error.get('message'))
        elif data.get('code') and data.get('message'):
            self.vendor_type = self.clean(str(data['code']))
            self.vendor_message = self.clean(data.get('message'))

    def snapshot(self, exception=None):
        return dict(provider=self.provider, http_status=self.status, content_type=self.content_type,
                    vendor_error_type=self.vendor_type, vendor_error_message=self.vendor_message,
                    last_sse_events=list(self.events), stage=self.stage,
                    exception_type=self.clean(type(exception).__name__) if exception is not None else None)
