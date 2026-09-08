"""Bounded, read-only loopback source for the single E108 user-test candidate.

Serves only a verified TARGET envelope and its exact immutable object hashes.
It is not a general directory server, deployment service or production feature.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import threading

from desktop.update_protocol import canonical, verify_release, SecurityError
from scripts.per_user_install import plain, read, keys, atomic
from scripts.install_product import configured


def make_server(feed, public_keys, app_id, *, port=48218):
    if not app_id.startswith('MentorFlow.UAT.'):
        raise SecurityError('test source requires a compiled UAT identity')
    feed = plain(feed)
    envelope = read(plain(feed / 'stable.json'))
    release = verify_release(envelope, public_keys, current='0.0.0')
    if release.signed['app_id'] != app_id:
        raise SecurityError('test source identity mismatch')
    origin = f'http://127.0.0.1:{port}'
    objects = {row['sha256']: row for row in release.rows.values()}
    if any(asset['url'] != origin + '/objects/' + asset['sha256']
           for asset in release.signed['full_release']['assets']):
        raise SecurityError('test source endpoint differs from signed asset origin')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_HEAD(self): self.respond(head=True)

        def do_GET(self): self.respond(head=False)

        def respond(self, *, head):
            if self.path == '/stable.json':
                if self.headers.get('Range'):
                    self.send_error(416); return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(envelope)))
                self.end_headers()
                if not head: self.wfile.write(envelope)
                return
            match = re.fullmatch('/objects/([0-9a-f]{64})', self.path)
            if not match or match[1] not in objects:
                self.send_error(404); return
            row = objects[match[1]]
            try:
                from scripts.update_source_reader import open_source
                with open_source(plain(feed / 'objects' / match[1])) as stream:
                    if os.fstat(stream.fileno()).st_size != row['size'] or hashlib.file_digest(stream, 'sha256').hexdigest() != match[1]:
                        raise SecurityError('immutable test object changed')
                    offset = 0
                    ranged = self.headers.get('Range')
                    if ranged:
                        if not re.fullmatch(r'bytes=\d+-', ranged):
                            self.send_error(416); return
                        offset = int(ranged[6:-1])
                        if offset >= row['size']:
                            self.send_error(416); return
                    self.send_response(206 if ranged else 200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(row['size'] - offset))
                    self.send_header('Accept-Ranges', 'bytes')
                    if ranged:
                        self.send_header('Content-Range', f"bytes {offset}-{row['size']-1}/{row['size']}")
                    self.end_headers()
                    if not head:
                        stream.seek(offset)
                        while block := stream.read(256 * 1024): self.wfile.write(block)
            except (OSError, SecurityError):
                self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed', type=Path, required=True)
    parser.add_argument('--port', type=int, default=48218)
    parser.add_argument('--seconds', type=int, default=7200)
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 28800 or not 1024 <= args.port <= 65535:
        raise ValueError('bounded local test source required')
    server, release = make_server(args.feed, keys(), configured().app_id, port=args.port)
    status = plain(args.feed).parent / 'test-source-status.json'
    atomic(status, canonical(dict(status='RUNNING', pid=os.getpid(), port=args.port,
                                 target=release.version, provider_calls=0)))
    timer = threading.Timer(args.seconds, server.shutdown)
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        timer.cancel()
        server.server_close()
        atomic(status, canonical(dict(status='STOPPED', pid=os.getpid(), provider_calls=0)))
    return dict(status='LOCAL_TEST_SOURCE_STOPPED')


if __name__ == '__main__':
    main()
