"""WebKit renders loopback HTML, exports PNG and respects native lifetime."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = b"<!doctype html><meta charset=utf-8><body data-mori-smoke=ready>MORI Mac WebKit</body>"
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        executable = Path(__file__).resolve().parents[1] / 'mori_krita/macos/MoriMacSmoke'
        subprocess.run([str(executable), f'http://127.0.0.1:{server.server_port}/connect/test'], check=True, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
