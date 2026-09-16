"""Prepare a private, typed Owner-approved intake manifest; never send or deploy.

Execution uses app.product_intake.execute with the existing authenticated relay
in the coordinator's process. No credentials or username/password CLI exists.
"""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.product_intake import prepare,attach_original_files,recover_indexed_archive_text


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--owner-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--archive-index',type=Path,action='append',default=[])
    parser.add_argument('--file-root',type=Path,action='append',default=[])
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(Path('E:/Codex/Temp').resolve()):
        parser.error('Private execution manifest must be inside E:/Codex/Temp')
    if output.exists():parser.error('Output already exists; reuse it or choose a fresh path')
    prepared=prepare(args.snapshot,expected_sha256=args.sha256,owner_sha256=args.owner_sha256)
    recovery=recover_indexed_archive_text(prepared,args.snapshot,args.archive_index,output.parent/'recovered-originals') if args.archive_index else {'originals':{}}
    value=attach_original_files(prepared,args.snapshot,recovered_originals=recovery['originals'],file_roots=args.file_root,recovery_root=output.parent/'recovered-originals')
    value['archive_recovery']=recovery
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,separators=(',',':'))
        stream.flush();os.fsync(stream.fileno())
    print(json.dumps(dict(status='PREPARED_NOT_SENT',records=len(value['records']),original_files=len(value['original_files']),missing_originals=len(value['missing_originals']))))


if __name__=='__main__':main()
