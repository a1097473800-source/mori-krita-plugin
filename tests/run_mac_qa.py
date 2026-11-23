"""Mac checks use real Krita Qt widgets, native WebKit and synthetic loopback data."""
import importlib
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import ModuleType
import unittest


def main(arguments):
    from run_update_qa import main as common
    common(arguments)
    output = Path(arguments[0])
    summary = json.loads((output / 'summary.json').read_text())
    module = importlib.import_module('test_mac_integration')
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(module))
    (output / 'test_mac_integration.txt').write_text(stream.getvalue())
    summary['test_mac_integration'] = {'passed': result.wasSuccessful(), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped)}
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    package = ModuleType('mori_mac_web_runtime')
    package.__path__ = [str(Path(__file__).resolve().parents[1] / 'mori_krita')]
    sys.modules[package.__name__] = package
    canvas_module = importlib.import_module(package.__name__ + '.webview')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = b'<!doctype html><body style="background:#222225;color:#bba5e9">MORI Native Mac Canvas</body>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    canvas = canvas_module.WebCanvas()
    events = []
    canvas.ready.connect(lambda: events.append('ready'))
    canvas.error.connect(lambda message: events.append('error: ' + message))
    try:
        canvas.resize(800, 600)
        canvas.show()
        canvas.set_active(True)
        assert canvas.start('http://127.0.0.1:%d/connect/native-qa' % server.server_port)
        deadline = time.monotonic() + 40
        while not events and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(.02)
        assert events == ['ready'], events
        assert canvas._mac_host and canvas._mac_host.handle and canvas._process is None
        canvas.resize(640, 420)
        QApplication.processEvents()
        canvas.set_active(False)
        canvas.set_active(True)
        canvas.stop()
        assert canvas._mac_host is None
        canvas.shutdown()
        summary['nativeQtWebKitEmbedding'] = {'passed': True, 'readyEvents': 1,
            'resizeAndVisibility': True, 'cleanShutdown': True, 'windowsHostStarted': False}
    except Exception as error:
        summary['nativeQtWebKitEmbedding'] = {'passed': False, 'error': repr(error), 'events': events}
    finally:
        canvas.shutdown()
        canvas.close()
        server.shutdown()
        server.server_close()
    summary['passed'] = all(value.get('passed') for key, value in summary.items() if key != 'passed')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
