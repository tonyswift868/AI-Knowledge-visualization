"""Pathless Vault metadata through the existing product producer."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .product_settings_api import producer
from .product_sync_contract import project

router=APIRouter(prefix='/api/product-resources')

class ResourceMetadataIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    original_filename: str = Field(min_length=1,max_length=300,pattern=r'^[^/\\\x00-\x1f]+$')
    media_type: str = Field(min_length=1,max_length=120,pattern=r'^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$')
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    size_bytes: int = Field(ge=0)
    source_kind: Literal['MANAGED','LINKED','MODEL']
    classification: Literal['USER_FILE','MODEL'] = 'USER_FILE'

@router.get('')
def read_resources(request: Request):
    with producer(request).connect() as c:
        return [dict(row) for row in c.execute('SELECT * FROM product_asset_metadata ORDER BY id')]

@router.put('/{resource_id}')
def put_resource(resource_id: str, body: ResourceMetadataIn, request: Request):
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}',resource_id):
        raise HTTPException(422,'RESOURCE_ID_INVALID')
    values={'id':resource_id,**body.model_dump()}
    project('product_asset_metadata',values)
    columns=list(values)
    with producer(request).connect() as c:
        names=','.join(columns);parameters=','.join('?' for _ in columns)
        updates=','.join(f'{key}=excluded.{key}' for key in columns if key!='id')
        differences=' OR '.join(f'{key}!=excluded.{key}' for key in columns if key!='id')
        c.execute(f'INSERT INTO product_asset_metadata ({names}) VALUES({parameters}) ON CONFLICT(id) DO UPDATE SET {updates} WHERE {differences}',tuple(values.values()))
    return values

@router.delete('/{resource_id}')
def delete_resource(resource_id: str, request: Request):
    with producer(request).connect() as c:
        c.execute('DELETE FROM product_asset_metadata WHERE id=?',(resource_id,))
    return {'id':resource_id,'deleted':True}
