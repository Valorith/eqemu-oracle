from __future__ import annotations

import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle import ingest  # noqa: E402


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    def read(self) -> bytes:
        return self.payload


class IngestFetchTest(unittest.TestCase):
    def test_fetch_json_retries_retryable_http_error(self) -> None:
        retryable_error = urllib.error.HTTPError("https://example.test/data.json", 502, "Bad Gateway", hdrs=None, fp=io.BytesIO())

        with patch("eqemu_oracle.ingest.urllib.request.urlopen", side_effect=[retryable_error, FakeResponse(b'{"ok": true}')]) as urlopen:
            with patch("eqemu_oracle.ingest.time.sleep") as sleep:
                result = ingest.fetch_json("https://example.test/data.json")
        retryable_error.close()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(ingest.FETCH_RETRY_DELAY_SECONDS)

    def test_fetch_json_does_not_retry_non_retryable_http_error(self) -> None:
        non_retryable_error = urllib.error.HTTPError("https://example.test/missing.json", 404, "Not Found", hdrs=None, fp=io.BytesIO())

        with patch("eqemu_oracle.ingest.urllib.request.urlopen", side_effect=non_retryable_error) as urlopen:
            with patch("eqemu_oracle.ingest.time.sleep") as sleep:
                with self.assertRaises(urllib.error.HTTPError):
                    ingest.fetch_json("https://example.test/missing.json")
        non_retryable_error.close()

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
