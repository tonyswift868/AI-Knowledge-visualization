"""S3-compatible byte storage seam for tenant-owned resource objects.

PostgreSQL remains the metadata authority; this adapter never invents a chunk
protocol. boto3's managed transfer handles multipart for large objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

@dataclass(frozen=True, slots=True)
class ObjectStorageConfig:
    bucket: str
    endpoint_url: str | None = None
    region: str = "auto"
    multipart_threshold: int = 8 * 1024 * 1024

class S3ObjectStorage:
    def __init__(self, config: ObjectStorageConfig, *, client=None, transfer_config=None):
        if not config.bucket or any(c in config.bucket for c in '/\\'):
            raise ValueError('valid bucket is required')
        if config.multipart_threshold < 5 * 1024 * 1024:
            raise ValueError('multipart threshold below S3 minimum')
        self.config = config
        if client is None:
            import boto3
            from boto3.s3.transfer import TransferConfig
            transfer_config = transfer_config or TransferConfig(
                multipart_threshold=config.multipart_threshold,
                multipart_chunksize=config.multipart_threshold)
            client = boto3.client('s3', endpoint_url=config.endpoint_url,
                                  region_name=config.region)
        self.client, self.transfer_config = client, transfer_config

    def upload(self, source: str | Path | BinaryIO, *, object_key: str,
               sha256: str, content_type: str, size_bytes: int) -> None:
        if not object_key or object_key.startswith('/') or '\\' in object_key:
            raise ValueError('object key must be a relative POSIX key')
        extra = {'ContentType': content_type, 'Metadata': {'sha256': sha256,
                 'size': str(size_bytes)}}
        kwargs = {'Bucket': self.config.bucket, 'Key': object_key,
                  'ExtraArgs': extra}
        if isinstance(source, (str, Path)):
            kwargs['Filename'] = str(source)
            self.client.upload_file(**kwargs, Config=self.transfer_config)
        else:
            kwargs['Fileobj'] = source
            self.client.upload_fileobj(**kwargs, Config=self.transfer_config)

    def download(self, *, object_key: str, destination: str | Path | BinaryIO) -> None:
        if not object_key or object_key.startswith('/') or '\\' in object_key:
            raise ValueError('object key must be a relative POSIX key')
        kwargs = {'Bucket': self.config.bucket, 'Key': object_key}
        if isinstance(destination, (str, Path)):
            self.client.download_file(**kwargs, Filename=str(destination), Config=self.transfer_config)
        else:
            self.client.download_fileobj(**kwargs, Fileobj=destination, Config=self.transfer_config)
