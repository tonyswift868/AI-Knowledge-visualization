"""Explicit non-secret configuration for a verified frozen test candidate."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

CONTRACT = 'MENTORFLOW_TEST_RUNTIME_V1'
ENV_KEY = 'MENTORFLOW_TEST_RUNTIME_CONFIGURATION'
FIELDS = {'contract', 'issuer', 'api_base_url', 'client_id', 'audience',
          'organization_id', 'workspace_id', 'tls_ca_path', 'tls_ca_sha256'}


def validate(value):
    if not isinstance(value, dict) or set(value) != FIELDS or value.get('contract') != CONTRACT:
        raise ValueError('TEST_RUNTIME_CONFIGURATION_REQUIRED')
    if any(not isinstance(v, str) or not v or len(v) > 1000 or any(ord(c) < 32 for c in v)
           for v in value.values()):
        raise ValueError('TEST_RUNTIME_CONFIGURATION_INVALID')
    for key in ('issuer', 'api_base_url'):
        url = urlsplit(value[key])
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or
                url.query or url.fragment or '\\' in value[key]):
            raise ValueError('TEST_RUNTIME_HTTPS_REQUIRED')
        _ = url.port
    if not Path(value['tls_ca_path']).is_absolute() or not re.fullmatch('[0-9a-f]{64}', value['tls_ca_sha256']):
        raise ValueError('TEST_RUNTIME_TLS_INVALID')
    return value


def apply_from_environment():
    """Before desktop imports; device roots override package sidecar roots.

    Launch authority remains in Workbench. This seam validates configuration
    shape and owned storage, and never imports a command or arbitrary env map.
    """
    from desktop.test_device_profile import read_profile, environment, plain
    root = os.environ.get('MENTORFLOW_TEST_DEVICE_ROOT')
    if not root: return False
    profile = read_profile(root)
    if os.environ.get('MENTORFLOW_TEST_DEVICE_GENERATION') != profile['generation']:
        raise ValueError('TEST_DEVICE_GENERATION_CHANGED')
    raw = os.environ.get(ENV_KEY, '')
    if len(raw) > 12000: raise ValueError('TEST_RUNTIME_CONFIGURATION_INVALID')
    value = validate(json.loads(raw))
    certificate = plain(Path(value['tls_ca_path']))
    if certificate.stat().st_size > 1024 * 1024:
        raise ValueError('TEST_RUNTIME_TLS_INVALID')
    if hashlib.sha256(certificate.read_bytes()).hexdigest() != value['tls_ca_sha256']:
        raise ValueError('TEST_RUNTIME_TLS_INVALID')
    os.environ.update(environment(root, profile))
    os.environ.update({
        'MENTORFLOW_DESKTOP_OIDC_ISSUER': value['issuer'],
        'MENTORFLOW_ENTERPRISE_API_BASE_URL': value['api_base_url'],
        'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID': value['client_id'],
        'MENTORFLOW_DESKTOP_OIDC_AUDIENCE': value['audience'],
        'MENTORFLOW_DESKTOP_ORGANIZATION_ID': value['organization_id'],
        'MENTORFLOW_DESKTOP_WORKSPACE_ID': value['workspace_id'],
        'MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT': '0',
        'MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE': '1',
        'MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE': '1',
        'SSL_CERT_FILE': str(certificate), 'REQUESTS_CA_BUNDLE': str(certificate),
    })
    return True
