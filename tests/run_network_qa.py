"""Official kritarunner entry: network mocks and anonymous read-only GET."""
import io
import json
from pathlib import Path
import sys
import time
import unittest


def main(arguments):
    from test_api import ApiTests
    from test_transport import TransportTests
    import importlib.util
    directory = Path(arguments[0])
    directory.mkdir(parents=True, exist_ok=True)
    suite = unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(ApiTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(TransportTests)))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "runtime": sys.version.split()[0], "anonymous_get_ms": []}
    try:
        spec = importlib.util.spec_from_file_location("runtime_network_api", Path(__file__).resolve().parents[1] / "mori_krita/api.py")
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        client = api.MoriClient("https://morinet.chat", timeout=15)
        for _ in range(3):
            started = time.perf_counter()
            response = client.session()
            if response.get("user"):
                raise RuntimeError("Unexpected user in anonymous request")
            report["anonymous_get_ms"].append(round((time.perf_counter()-started)*1000, 3))
        client.clear_session()
    except Exception as failure:
        report["read_only_probe_error"] = type(failure).__name__
    (directory / "krita-network-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    (directory / "krita-network-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
