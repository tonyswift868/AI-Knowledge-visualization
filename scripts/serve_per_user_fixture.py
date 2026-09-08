"""Bounded loopback-only signed-release fixture with exact HTTP body accounting."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=3600);args=parser.parse_args()
    if not 1<=args.seconds<=5400:raise ValueError('bounded fixture lifetime required')
    root=args.work.absolute()/'release';mapping=json.loads((root/'asset-map.json').read_bytes())
    log=args.work/('http-'+str(time.time_ns())+'.jsonl');lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            sent=0;offset=0;category='asset';error=None
            try:
                if self.path=='/stable.json':
                    label=json.loads((args.work/'feed-selection.json').read_bytes())['release']
                    if label not in ('base','target','bad'):raise ValueError('unknown fixture release')
                    path=root/(label+'.json');category='metadata'
                elif re.fullmatch('/objects/[0-9a-f]{64}',self.path):
                    path=(root/mapping[self.path.rsplit('/',1)[1]]).absolute()
                    if not path.is_relative_to(root):raise ValueError('asset-map escape')
                else:self.send_error(404);return
                size=path.stat().st_size;value=self.headers.get('Range')
                if value:
                    if category!='asset' or not re.fullmatch('bytes=[0-9]+-',value):self.send_error(416);return
                    offset=int(value[6:-1])
                    if offset>=size:self.send_error(416);return
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length',str(size-offset));self.send_header('Accept-Ranges','bytes')
                if offset:self.send_header('Content-Range',f'bytes {offset}-{size-1}/{size}')
                self.end_headers()
                with path.open('rb') as stream:
                    stream.seek(offset)
                    while chunk:=stream.read(256*1024):sent+=self.wfile.write(chunk)
                self.wfile.flush()
            except (OSError,ValueError,KeyError) as exc:error=type(exc).__name__;self.close_connection=True
            finally:
                with lock,log.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(path=self.path,category=category,range_offset=offset,body_bytes=sent,error=error))+'\n')
    server=ThreadingHTTPServer(('127.0.0.1',48208),Handler);server.daemon_threads=True
    timer=threading.Timer(args.seconds,server.shutdown);timer.start()
    print(json.dumps(dict(status='LOCAL_FIXTURE_RUNNING',log=str(log))),flush=True)
    try:server.serve_forever(poll_interval=.5)
    finally:timer.cancel();server.server_close()


if __name__=='__main__':main()
