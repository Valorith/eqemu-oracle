from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle import operations  # noqa: E402
from eqemu_oracle.utils import dump_json  # noqa: E402


class OperationsTest(unittest.TestCase):
    def test_remove_tree_uses_cross_platform_onerror_handler(self) -> None:
        target = Path("dummy")

        with patch("eqemu_oracle.operations.shutil.rmtree") as rmtree:
            with patch.object(Path, "exists", return_value=True):
                operations._remove_tree(target)

        self.assertEqual(rmtree.call_args.args[0], target)
        self.assertIn("onerror", rmtree.call_args.kwargs)
        self.assertNotIn("onexc", rmtree.call_args.kwargs)

    def test_refresh_dataset_clears_full_committed_state_and_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_root = root / "base"
            merged_root = root / "merged"
            overlay_root = root / "overlay"
            for path in (base_root / "quest-api", merged_root / "schema", overlay_root / "base"):
                path.mkdir(parents=True, exist_ok=True)

            with patch("eqemu_oracle.operations.BASE_ROOT", base_root):
                with patch("eqemu_oracle.operations.MERGED_ROOT", merged_root):
                    with patch("eqemu_oracle.operations.OVERLAY_ROOT", overlay_root):
                        with patch("eqemu_oracle.operations.write_base_dataset") as write_base_dataset:
                            with patch("eqemu_oracle.operations.write_merged_dataset", return_value={"merge_scope": "all"}) as write_merged_dataset:
                                manifest = operations.refresh_dataset(scope="all", mode="committed")

            self.assertEqual(manifest["merge_scope"], "all")
            self.assertFalse(base_root.exists())
            self.assertFalse(merged_root.exists())
            self.assertFalse(overlay_root.exists())
            write_base_dataset.assert_called_once_with(base_root, scope="all")
            write_merged_dataset.assert_called_once_with(base_root, merged_root, scope="all")

    def test_prune_schema_extensions_dataset_rebuilds_schema_only_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_root = root / "base"
            merged_root = root / "merged"
            dump_json(base_root / "schema" / "index.json", [])
            dump_json(merged_root / "schema" / "index.json", [])

            with patch("eqemu_oracle.operations.BASE_ROOT", base_root):
                with patch("eqemu_oracle.operations.MERGED_ROOT", merged_root):
                    with patch("eqemu_oracle.operations.prune_stale_schema_extensions", return_value={"removed_count": 1}) as prune_mock:
                        with patch("eqemu_oracle.operations.write_merged_dataset", return_value={"merge_scope": "schema"}) as write_merged_dataset:
                            result, manifest = operations.prune_schema_extensions_dataset(apply=True, mode="committed")

        prune_mock.assert_called_once_with(base_root, apply=True)
        write_merged_dataset.assert_called_once_with(base_root, merged_root, scope="schema")
        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(manifest["merge_scope"], "schema")

    def test_maintenance_lock_serializes_callers(self) -> None:
        entered: list[str] = []
        released = threading.Event()
        first_ready = threading.Event()
        second_finished = threading.Event()

        def first_worker() -> None:
            with operations.maintenance_lock(timeout_seconds=1.0):
                entered.append("first")
                first_ready.set()
                released.wait(timeout=1.0)

        def second_worker() -> None:
            with operations.maintenance_lock(timeout_seconds=1.0):
                entered.append("second")
            second_finished.set()

        thread = threading.Thread(target=first_worker)
        thread.start()
        self.assertTrue(first_ready.wait(timeout=1.0))

        second_thread = threading.Thread(target=second_worker)
        second_thread.start()
        time.sleep(0.1)
        self.assertEqual(entered, ["first"])

        released.set()
        thread.join(timeout=1.0)
        second_thread.join(timeout=1.0)

        self.assertTrue(second_finished.is_set())
        self.assertEqual(entered, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
