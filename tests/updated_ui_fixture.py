"""Isolated built-UI -> real bridge -> real workspace integration fixture.

Every model reply and Krita pixel is synthetic. The original website is read
only; its opt-in fixture is copied with absolute module imports into QA output.
Run with --upstream <local-canvas> --output <qa/updated-web-ui>. Stop with Ctrl+C,
POST to the printed control shutdown URL, or create <output>/stop.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "mori_krita" / filename)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def generated_backend(upstream, destination):
    original = upstream / "tests" / "image-actions-ui-fixture.mjs"
    source = original.read_text("utf-8")
    import re
    source = re.sub(r"from '([.][.]/server/[^']+)'", lambda match: "from '" + (original.parent / match[1]).resolve().as_uri() + "'", source)
    source = source.replace("path.dirname(path.dirname(fileURLToPath(import.meta.url)))", json.dumps(str(upstream)))
    # Only the fixture's in-memory fetch receives these URLs. Nothing calls the
    # network: even public-image imports return generated PNG pixels.
    injection = """
  if(target.protocol==='https:'&&target.hostname==='i.pinimg.com'){
    const bytes=png(320,240,{variant:1});
    return new Response(bytes,{headers:{'Content-Type':'image/png','Content-Length':String(bytes.length)}});
  }
"""
    source = source.replace("  if(target.protocol!=='fixture:'", injection + "  if(target.protocol!=='fixture:'", 1)
    chat = """
  if(target.pathname==='/v1/responses'){
    const request=JSON.parse(options.body||'{}'),tool=request.tools?.find(item=>item.name==='extract_style');
    const features={subject:'Synthetic product',lighting:'Soft side light',material:'Matte',composition:'Clean centered layout',palette:'Purple and grey',mood:'Calm',negative:'Clutter',tags:['fixture'],prompt:'Soft side light, matte material, purple and grey, clean centered composition.'};
    const output=tool?[{id:'fc-fixture',type:'function_call',call_id:'call-fixture',name:'extract_style',arguments:JSON.stringify(features)}]:[{type:'message',role:'assistant',content:[{type:'output_text',text:'Isolated fixture reply. No external model request was sent.'}]}];
    await wait(200,options.signal);
    return new Response('data: '+JSON.stringify({type:'response.completed',response:{status:'completed',output}})+'\\n\\n',{headers:{'Content-Type':'text/event-stream'}});
  }
"""
    source = source.replace("  if(!['/v1/images/edits'", chat + "  if(!['/v1/images/edits'", 1)
    source = source.replace("    if(await workspace.handle(req,res,url))return;", "    if(url.pathname==='/api/assets'&&req.method==='POST')await wait(850);\n    if(await workspace.handle(req,res,url))return;", 1)
    destination.write_text(source, "utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend-port", type=int, default=0)
    args = parser.parse_args()
    upstream, output = args.upstream.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    web_root = PROJECT / "mori_krita" / "web"
    if not (web_root / "index.html").is_file():
        raise RuntimeError("Build the plugin frontend before running this fixture")
    stop_file = output / "stop"
    if stop_file.exists():
        stop_file.rename(output / ("stop-previous-" + str(time.time_ns())))
    backend_script = output / "fixture-backend.mjs"
    generated_backend(upstream, backend_script)
    backend_port = args.backend_port or free_port()
    backend_url = "http://127.0.0.1:" + str(backend_port)
    environment = dict(os.environ)
    for key in ("IMAGE_ACTIONS_UI_SOURCE", "IMAGE_ACTIONS_UI_OBJECTS", "IMAGE_ACTIONS_UI_LARGE"):
        environment.pop(key, None)
    environment["IMAGE_ACTIONS_UI_PORT"] = str(backend_port)
    stopped = threading.Event()
    status_lock = threading.RLock()
    statuses, imports, errors = [], [], []
    backend_log = (output / "backend.log").open("w", encoding="utf-8")
    backend = subprocess.Popen(["node", str(backend_script)], cwd=upstream, env=environment,
                               stdout=backend_log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    gateway = control = None
    temporary = tempfile.TemporaryDirectory(prefix="mori-krita-ui-")
    try:
        deadline = time.monotonic() + 20
        while True:
            if backend.poll() is not None:
                raise RuntimeError("Fixture backend exited; see backend.log")
            try:
                with urllib.request.urlopen(backend_url + "/api/auth/session", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Fixture backend did not become ready; see backend.log")
                time.sleep(.1)
        api = module("mori_updated_fixture_api", "api.py")
        transport = module("mori_updated_fixture_gateway", "gateway.py")
        client = api.MoriClient(backend_url, timeout=10, _proxy_config={})
        session = client.session()
        if session.get("enabled") is not False:
            raise RuntimeError("Only the explicitly unauthenticated synthetic fixture is allowed")
        seed = client.request("GET", "/api/state")
        assets = client.assets()
        if not assets:
            raise RuntimeError("Upstream fixture did not seed synthetic images")
        pixels = client.download_asset(assets[0]["url"])
        synthetic = SimpleNamespace(image_bytes=pixels, png_bytes=pixels, mime="image/png",
                                    width=assets[0]["width"], height=assets[0]["height"], metrics={},
                                    layer_name="Synthetic fixture layer", document_name="Fixture document")
        image_directory = output / "imported-images"
        image_directory.mkdir(exist_ok=True)

        def write_ledger():
            (output / "import-ledger.json").write_text(json.dumps(imports, indent=2), "utf-8")

        def invoke(operation, data):
            if operation == "target":
                return "synthetic-krita-document"
            if operation in ("snapshot", "capture"):
                return synthetic
            if operation == "import":
                with status_lock:
                    path = image_directory / ("layer-%03d.png" % (len(imports) + 1))
                    path.write_bytes(data["bytes"])
                    imports.append({"name": data["name"], "target": data.get("target"),
                                    "force_new": data.get("force_new", False), "bytes": len(data["bytes"]),
                                    "sha256": hashlib.sha256(data["bytes"]).hexdigest(), "path": str(path)})
                    write_ledger()
                return "synthetic-krita-document"
            raise RuntimeError("Unsupported fixture GUI operation: " + operation)

        def notify(message):
            with status_lock:
                statuses.append(str(message))
                del statuses[:-50]

        gateway = transport.MoriGateway(client, web_root, temporary.name, invoke, notify)
        gateway._sync_identity()
        key = gateway._account_key()
        gateway.store.put(key, {**seed, "expectedRevision": 0, "name": "Krita integration fixture"})
        gateway.start()
        token = secrets.token_urlsafe(20)

        class Control(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def result(self, value, status=200):
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def route(self):
                prefix = "/" + token
                if self.headers.get("Host") != "127.0.0.1:" + str(control.server_port):
                    return None
                if self.headers.get("Origin") not in (None, "http://127.0.0.1:" + str(control.server_port)):
                    return None
                return self.path[len(prefix):] if self.path.startswith(prefix + "/") else None

            def do_GET(self):
                if self.route() != "/status":
                    self.result({"error": "Fixture route not found"}, 404)
                    return
                with status_lock:
                    self.result({"imports": imports, "statuses": statuses, "errors": errors,
                                 "activeCanvas": gateway.active_canvas, "metrics": gateway.last_reference_metrics,
                                 "synthetic": True, "external_model_calls": 0, "user_document_reads": 0})

            def do_POST(self):
                route = self.route()
                if route == "/shutdown":
                    self.result({"stopping": True})
                    stopped.set()
                    return
                if route not in ("/capture", "/import-selected"):
                    self.result({"error": "Fixture route not found"}, 404)
                    return

                def run():
                    try:
                        if route == "/capture":
                            gateway.capture("auto", mode="fast", prepare=lambda value, **kwargs: value, display=True)
                        else:
                            gateway.import_selected()
                    except Exception as error:
                        with status_lock:
                            errors.append(str(error))

                threading.Thread(target=run, daemon=True).start()
                self.result({"queued": True})

        control = ThreadingHTTPServer(("127.0.0.1", 0), Control)
        threading.Thread(target=control.serve_forever, daemon=True).start()
        control_url = "http://127.0.0.1:" + str(control.server_port) + "/" + token
        report = {"url": gateway.url, "origin": gateway.origin, "backend_url": backend_url,
                  "control_url": control_url, "pid": os.getpid(), "backend_pid": backend.pid,
                  "synthetic": True, "external_model_calls": 0, "user_document_reads": 0,
                  "upstream": str(upstream), "web_root": str(web_root), "seed_assets": len(assets)}
        (output / "server.json").write_text(json.dumps(report, indent=2), "utf-8")
        write_ledger()
        print(json.dumps(report), flush=True)
        for kind in (signal.SIGINT, signal.SIGTERM):
            signal.signal(kind, lambda *_: stopped.set())
        while not stopped.wait(.25):
            if stop_file.exists():
                break
            if backend.poll() is not None:
                raise RuntimeError("Fixture backend stopped unexpectedly; see backend.log")
    finally:
        if control is not None:
            control.shutdown()
            control.server_close()
        if gateway is not None:
            gateway.close()
        if backend.poll() is None:
            try:
                request = urllib.request.Request(backend_url + "/__fixture/shutdown", data=b"", method="POST",
                                                 headers={"Origin": backend_url})
                urllib.request.urlopen(request, timeout=3).close()
                backend.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                backend.terminate()
                backend.wait(timeout=5)
        backend_log.close()
        temporary_path = Path(temporary.name).resolve()
        if temporary_path.parent != Path(tempfile.gettempdir()).resolve() or not temporary_path.name.startswith("mori-krita-ui-"):
            raise RuntimeError("Unexpected fixture temporary directory")
        temporary.cleanup()


if __name__ == "__main__":
    main()
