"""Explicit non-secret account preferences; never exports QSettings."""
import json
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict
from .enterprise_tenancy.profile_settings_sync import validate as validate_profile

router = APIRouter(prefix='/api/product-settings')
DESKTOP_ACCOUNT_KEYS = frozenset({'analogy/keywords', 'task-colors'})


class SettingInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str | bool | int | float | dict[str,str] | None


def validate_setting(key, value):
    if key == 'analogy/keywords':
        if value is not None and (not isinstance(value,str) or len(value)>4000):
            raise ValueError('invalid interests')
    elif key == 'task-colors':
        if value is not None and (not isinstance(value,dict) or len(value)>1000 or
            any(not isinstance(k,str) or len(k)>160 or not isinstance(v,str) or len(v)>64 for k,v in value.items())):
            raise ValueError('invalid task colors')
    else:
        validate_profile({key:value})
    return value


def producer(request):
    value = getattr(request.app.state,'product_sync',None)
    if value is None:
        raise HTTPException(409, 'ACCOUNT_SYNC_REQUIRED')
    return value


@router.get('')
def read_settings(request: Request):
    return producer(request).runtime_states('product_account_settings')


@router.put('/{key:path}')
def write_setting(key: str, body: SettingInput, request: Request):
    try:
        value = validate_setting(key,body.value)
    except ValueError:
        raise HTTPException(422,'SETTING_NOT_SUPPORTED') from None
    producer(request).complete_command(None,None,None,[('product_account_settings',key,value)])
    return {'key':key,'value':value}
