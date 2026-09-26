"""Authenticated, read-only operator catalogs; bytes and ETags share one read."""
import hashlib
import json
import os
from pathlib import Path

from fastapi import APIRouter, Request, Response

from .identity.oidc import IdentityError
from .identity.runtime import identity_error_response


router = APIRouter(prefix='/api/enterprise/catalog')
MAX_CATALOG_BYTES = 1024 * 1024


def _invalid_constant(value):
    raise ValueError('non-JSON number')


def _catalog(request, filename):
    try:
        # Same verified actor/workspace boundary as structured_sync_api.call.
        if (getattr(request.state, 'actor_context', None) is None
                or getattr(request.state, 'tenant_context', None) is None):
            raise IdentityError('ENTERPRISE_AUTH_REQUIRED', 'Authenticated workspace required', status=401)
        path = Path(os.environ.get('MENTORFLOW_CATALOG_DIR', '/run/mentorflow/catalog')) / filename
        try:
            with path.open('rb') as stream:
                content = stream.read(MAX_CATALOG_BYTES + 1)
        except FileNotFoundError:
            raise IdentityError('CATALOG_NOT_CONFIGURED', '目录文件尚未配置。', status=404) from None
        except OSError:
            raise IdentityError('CATALOG_INVALID', '目录文件暂不可用。', status=503) from None
        try:
            if len(content) > MAX_CATALOG_BYTES:
                raise ValueError('catalog too large')
            value = json.loads(content, parse_constant=_invalid_constant)
            if not isinstance(value, dict) or 'version' not in value:
                raise ValueError('versioned object required')
        except (ValueError, UnicodeError, RecursionError):
            raise IdentityError('CATALOG_INVALID', '目录文件格式无效。', status=503) from None
        etag = '"' + hashlib.sha256(content).hexdigest() + '"'
        headers = {'ETag': etag, 'Cache-Control': 'private, max-age=300'}
        candidates = request.headers.get('If-None-Match', '').split(',')
        if any(candidate.strip() == '*' or candidate.strip().removeprefix('W/') == etag for candidate in candidates):
            return Response(status_code=304, headers=headers)
        return Response(content=content, media_type='application/json', headers=headers)
    except IdentityError as exc:
        response = identity_error_response(request, exc)
        response.headers['Cache-Control'] = 'no-store'
        return response


@router.get('/source-whitelist')
def source_whitelist(request: Request):
    return _catalog(request, 'source-whitelist.json')


@router.get('/model-prices')
def model_prices(request: Request):
    return _catalog(request, 'model-prices.json')
