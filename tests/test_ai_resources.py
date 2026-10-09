"""AI jobs capture settings and close HTTP resources without a live provider."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from ai.client import AIClient, AIRequestError
from config import Config


class AIResourceTests(unittest.TestCase):
    def test_response_error_redacts_full_key_before_preview_cutoff(self):
        cfg = Config()
        secret = "private-cutoff-sentinel-never-log"
        cfg.values.update(AI_ENABLE=True, AI_API_KEY=secret)
        scenarios = [(429, "x" * 190 + secret, None), (401, "x" * 290 + secret, None),
                     (200, "x" * 190 + secret, ValueError("not json")),
                     (200, "", {"echo": "x" * 280 + secret})]
        for status, text, data in scenarios:
            with self.subTest(status=status, data=data):
                def decode():
                    if isinstance(data, Exception):
                        raise data
                    return data
                response = SimpleNamespace(status_code=status, text=text, json=decode)
                with patch("ai.client.requests.Session.post", return_value=response):
                    with self.assertRaises(RuntimeError) as raised:
                        AIClient(cfg)._once({}, {})
                self.assertNotIn("private-", str(raised.exception))

    def test_worker_rejects_backlog_and_shutdown_cancels_queued_work_without_waiting(self):
        from ui.ai_worker import AIWorker
        entered, release = threading.Event(), threading.Event()
        pool = AIWorker(workers=1, pending=1)
        writes = []
        def blocked():
            entered.set()
            release.wait(2)
        try:
            running = pool.submit(blocked)
            self.assertTrue(entered.wait(1))
            pending = pool.submit(lambda: writes.append("unexpected queued work"))
            with self.assertRaises(RuntimeError):
                pool.submit(lambda: None)
            pool.shutdown()
            self.assertFalse(running.done())
            self.assertTrue(pending.cancelled())
            release.set()
            running.result(1)
            pool.shutdown(wait=True)
            self.assertEqual(writes, [])
        finally:
            release.set()
            pool.shutdown(wait=True)

    def test_uncancelled_network_failure_retries_with_a_new_closed_session(self):
        cfg = Config()
        cfg.values.update(AI_ENABLE=True, AI_API_KEY="offline-key", AI_MAX_RETRY=2)
        sessions, closed = [], []
        class Session:
            def __enter__(self):
                sessions.append(self)
                return self
            def __exit__(self, *args):
                closed.append(self)
            def post(self, *args, **kwargs):
                if len(sessions) == 1:
                    raise requests.ConnectionError("offline retry")
                return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "retried"}}]})
        with patch("ai.client.requests.Session", Session), patch("utils.retry.time.sleep"):
            self.assertEqual(AIClient(cfg).ask("s", "u"), "retried")
        self.assertEqual(len(sessions), 2)
        self.assertEqual(closed, sessions)

    def test_captured_config_survives_settings_and_environment_replacement(self):
        from ui.ai_worker import CapturedConfig
        cfg = Config()
        cfg.values.update(AI_ENABLE=True, AI_API_KEY="before", AI_MODEL="before-model")
        with patch.dict("os.environ", {"AI_API_KEY": "env-before", "AI_MODEL": "env-model"}):
            captured = CapturedConfig(cfg)
        cfg.values.update(AI_API_KEY="after", AI_MODEL="after-model", AI_ENABLE=False)
        with patch.dict("os.environ", {"AI_API_KEY": "env-after", "AI_MODEL": "env-after-model"}):
            self.assertEqual(captured.ai_api_key, "env-before")
            self.assertEqual(captured.ai_model, "env-model")
            self.assertTrue(captured.get("AI_ENABLE"))

    def test_cancel_after_network_error_never_starts_another_http_attempt(self):
        cfg = Config()
        cfg.values.update(AI_ENABLE=True, AI_API_KEY="offline", AI_MAX_RETRY=3)
        cancelled = threading.Event()
        posts = []
        def post(*args, **kwargs):
            posts.append(threading.get_ident())
            cancelled.set()
            raise requests.ConnectionError("offline")
        with patch("ai.client.requests.Session.post", side_effect=post), patch("utils.retry.time.sleep"):
            client = AIClient(cfg)
            client.cancel_event = cancelled
            with self.assertRaises(RuntimeError):
                client.ask("s", "u")
        self.assertEqual(len(posts), 1)

    def test_parallel_calls_own_and_close_their_sessions(self):
        cfg = Config()
        cfg.values.update(AI_ENABLE=True, AI_API_KEY="offline-key", AI_MAX_RETRY=1)
        owners, closed = [], []
        barrier = threading.Barrier(2)
        class Session:
            def __init__(self):
                self.owner = threading.get_ident()
                owners.append(self.owner)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                closed.append((self.owner, threading.get_ident()))
            def post(self, *args, **kwargs):
                barrier.wait(1)
                return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "ok"}}]})
        with patch("ai.client.requests.Session", Session):
            client = AIClient(cfg)
            threads = [threading.Thread(target=client.ask, args=("s", "u")) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(2)
        self.assertEqual(len(owners), 2)
        self.assertEqual(set(owners), {t.ident for t in threads})
        self.assertEqual(sorted(closed), sorted((owner, owner) for owner in owners))

    def test_network_error_redacts_key_at_source_after_desktop_closes(self):
        cfg = Config()
        cfg.values.update(AI_ENABLE=True, AI_API_KEY="offline-private-key")
        with patch("ai.client.requests.Session.post", side_effect=requests.ConnectionError("echo offline-private-key")):
            client = AIClient(cfg)
            with self.assertRaises(AIRequestError) as raised:
                client._once({}, {})
        self.assertNotIn("offline-private-key", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
