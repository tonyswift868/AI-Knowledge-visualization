import io
import pytest
from app.enterprise_tenancy.object_storage import ObjectStorageConfig, S3ObjectStorage

class FakeS3:
    def __init__(self): self.calls=[]
    def upload_fileobj(self, **kw): self.calls.append(('upload_fileobj', kw)); kw['Fileobj'].read()
    def download_fileobj(self, **kw): self.calls.append(('download_fileobj', kw)); kw['Fileobj'].write(b'payload')
    def upload_file(self, **kw): self.calls.append(('upload_file', kw))
    def download_file(self, **kw): self.calls.append(('download_file', kw))

def test_minio_compatible_stream_roundtrip_and_metadata():
    fake=FakeS3(); s=S3ObjectStorage(ObjectStorageConfig('disposable', endpoint_url='http://127.0.0.1:9000'), client=fake)
    s.upload(io.BytesIO(b'x'), object_key='organizations/o/workspaces/w/resource/r/file.pdf', sha256='a'*64, content_type='application/pdf', size_bytes=1)
    out=io.BytesIO(); s.download(object_key='organizations/o/workspaces/w/resource/r/file.pdf', destination=out)
    assert out.getvalue()==b'payload' and fake.calls[0][1]['ExtraArgs']['Metadata']=={'sha256':'a'*64,'size':'1'}

@pytest.mark.parametrize('key', ['', '/absolute', r'bad\\key'])
def test_keys_are_scoped_and_no_path_traversal(key):
    with pytest.raises(ValueError): S3ObjectStorage(ObjectStorageConfig('disposable'), client=FakeS3()).download(object_key=key, destination=io.BytesIO())
