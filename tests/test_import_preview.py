"""Import previews must be local and visible before any slow upload completes."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("import_preview_gateway", Path(__file__).resolve().parents[1]/"mori_krita/gateway.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PNG = b"\x89PNG\r\n\x1a\nsynthetic-preview"


class Client:
    base_url = "https://qa.invalid"
    user_id = "qa-user"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.fail = False
        self.calls = []

    def upload_image(self, data, name):
        self.started.set()
        if not self.release.wait(3):
            raise TimeoutError("Mock upload did not finish")
        if self.fail:
            raise RuntimeError("Mock upload failed")
        return {"id":"uploaded","url":"/media/abcdef.png","name":name,"width":512,"height":256}

    def raw_request(self, *args, **kwargs):
        self.calls.append(args)
        raise AssertionError("Unexpected network request")

    def clear_session(self):
        self.user_id = None


class ImportPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = Client()
        self.gateway = module.MoriGateway(self.client,self.root,self.root/"state",self.invoke)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def invoke(self, op, data):
        self.assertEqual(op,"capture")
        return SimpleNamespace(png_bytes=PNG,width=512,height=256,layer_name="QA",document_name="QA")

    def tearDown(self):
        self.client.release.set()
        self.pool.shutdown(wait=True)
        self.gateway.close()
        self.temp.cleanup()

    def begin(self):
        future = self.pool.submit(self.gateway.capture,"auto",display=True)
        self.assertTrue(self.client.started.wait(2))
        self.assertFalse(future.done())
        return future

    def test_preview_is_local_and_available_while_upload_is_still_blocked(self):
        future = self.begin()
        preview = next(event for event in self.gateway.events if event["type"]=="capture_pending")
        self.assertEqual(preview["asset"]["width"],512)
        status, headers, body = self.gateway.handle("GET",preview["asset"]["url"])
        self.assertEqual(status,200)
        self.assertEqual(headers["content-type"],"image/png")
        self.assertEqual(body,PNG)
        self.assertEqual(self.client.calls,[])
        self.client.release.set();future.result(2)
        done = self.gateway.events[-1]
        self.assertEqual(done["type"],"asset")
        self.assertEqual(done["importId"],preview["importId"])
        self.assertEqual(self.gateway.pending_captures,{})

    def test_generation_cannot_ignore_a_reference_that_is_still_uploading(self):
        future = self.begin()
        with self.assertRaises(module.GatewayError) as raised:
            self.gateway.handle("POST","/api/generate",json.dumps({"clientRequestId":"test-request-123"}).encode())
        self.assertEqual(raised.exception.status,409)
        self.assertEqual(self.client.calls,[])
        self.client.release.set();future.result(2)

    def test_upload_failure_ends_pending_state_and_reports_the_same_preview(self):
        self.client.fail=True
        future = self.begin()
        preview = self.gateway.events[0]
        self.client.release.set()
        with self.assertRaises(RuntimeError):
            future.result(2)
        self.assertEqual(self.gateway.pending_captures,{})
        self.assertEqual(self.gateway.events[-1]["type"],"capture_failed")
        self.assertEqual(self.gateway.events[-1]["importId"],preview["importId"])


if __name__=="__main__":
    unittest.main()
