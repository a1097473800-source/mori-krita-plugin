"""Saved-session recovery before a native send, with offline Qt doubles."""
import io
import json
from pathlib import Path
import unittest
from unittest import mock

import test_selection_controller as fixture


@unittest.skipIf(fixture.QWidget is None, "Run native recovery tests inside Krita")
class NativeSessionRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.SelectionControllerTests.setUpClass()

    def setUp(self):
        self.fixture = fixture.SelectionControllerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.controller = self.fixture.controller
        self.panel = self.fixture.show_panel()
        self.fixture.work.clear()
        self.client = self.fixture.docker._gateway.client
        self.client.user_id = None
        self.client.session_restored = True
        self.client.session_version = 1
        self.controller._login = mock.Mock(return_value=False)

    def validate(self, user="qa-user"):
        self.client.session_restored = False
        self.client.user_id = user
        self.client.session_version += 1
        return {"enabled": True, "user": {"id": user} if user else None}

    def start(self):
        self.controller.submit(self.fixture.payload())
        self.assertTrue(self.panel.is_busy())
        self.assertEqual(self.controller._login.call_count, 0)
        self.assertEqual(self.fixture.docker._bridge.snapshots, [])
        self.assertEqual(len(self.fixture.work), 1)
        return self.fixture.work.pop()

    def test_valid_saved_cookie_validates_once_before_capturing_and_sending(self):
        self.client.session = self.validate
        work, callback = self.start()
        self.controller.submit(self.fixture.payload())
        self.assertEqual(self.fixture.work, [])
        callback(work(), None)
        self.assertFalse(self.controller._restoring_session)
        self.assertTrue(self.controller._task_id)
        self.assertEqual(self.fixture.docker._bridge.snapshots, ["selection"])
        self.assertEqual(self.controller._login.call_count, 0)
        self.assertEqual(len(self.fixture.work), 1)  # One generation, not executed.

    def test_expired_cookie_opens_sign_in_after_validation_without_capturing(self):
        self.client.session = lambda: self.validate(None)
        work, callback = self.start()
        callback(work(), None)
        self.controller._login.assert_called_once()
        self.assertFalse(self.panel.is_busy())
        self.assertEqual(self.fixture.docker._bridge.snapshots, [])

    def test_session_401_is_a_sign_in_prompt_instead_of_a_retry_loop(self):
        class Expired(Exception):
            status = 401

        def expired():
            self.validate(None)
            raise Expired("expired")

        self.client.session = expired
        work, callback = self.start()
        callback(work(), None)
        self.controller._login.assert_called_once()
        self.assertFalse(self.controller._restoring_session)

    def test_network_error_keeps_saved_login_and_does_not_prompt_or_send(self):
        _, callback = self.start()
        callback(None, "offline")
        self.assertTrue(self.client.session_restored)
        self.assertFalse(self.panel.is_busy())
        self.assertEqual(self.controller._login.call_count, 0)
        self.assertEqual(self.fixture.docker._bridge.snapshots, [])
        self.assertIn("offline", self.panel._status_text)

    def test_reconnected_gateway_cannot_receive_a_stale_restoration(self):
        self.client.session = self.validate
        work, callback = self.start()
        version = work()
        self.fixture.docker._gateway = fixture.FakeGateway()
        callback(version, None)
        self.assertEqual(self.fixture.docker._bridge.snapshots, [])
        self.assertEqual(self.controller._login.call_count, 0)

    def test_account_change_after_response_cannot_send_the_old_prompt(self):
        self.client.session = self.validate
        work, callback = self.start()
        version = work()
        self.client.session_version += 1
        callback(version, None)
        self.assertEqual(self.fixture.docker._bridge.snapshots, [])
        self.assertIsNone(self.controller._task_id)

    def test_document_or_selection_changed_during_validation_needs_another_click(self):
        for changed in ("document", "selection"):
            with self.subTest(changed=changed):
                self.client.user_id = None
                self.client.session_restored = True
                original = self.fixture.anchor
                self.client.session = self.validate
                work, callback = self.start()
                version = work()
                replacement = self.fixture.make_anchor(self.fixture.canvas, object(), "b" if changed == "document" else "a")
                if changed == "selection":
                    replacement.selection_rect = fixture.QRectF(10, 20, 90, 80)
                self.fixture.anchor = replacement
                callback(version, None)
                self.assertEqual(self.fixture.docker._bridge.snapshots, [])
                self.assertIsNone(self.controller._task_id)
                self.fixture.anchor = original


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeSessionRestoreTests))
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors)}
    (output / "native-session-restore-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "native-session-restore-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    return report
