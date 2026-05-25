"""Tests for thread leak detector and flaky test auto-quarantine."""

import sys
from pathlib import Path


def test_thread_leak_detection():
    """Test that a leaked background thread is caught by the detector."""
    # Write a test file that leaks a thread inside the tests directory so conftest.py is loaded
    test_file = Path(__file__).parent / "tmp_leak_test.py"
    test_file.write_text(
        """
import threading
import time

def test_leaky():
    def _run():
        time.sleep(1)
    t = threading.Thread(target=_run, name="LeakedTestThread")
    t.start()
    # Leaks the thread
""",
        encoding="utf-8",
    )

    try:
        # Run pytest on the temporary test file in a subprocess
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-vv", "-o", "addopts="],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),  # Run from project root so conftest.py is loaded
        )

        assert (
            "Thread leak detected: 1 thread(s) leaked during test" in result.stdout
            or "Thread leak detected" in result.stderr
        )
        assert "LeakedTestThread" in result.stdout or "LeakedTestThread" in result.stderr
        assert result.returncode != 0
    finally:
        if test_file.exists():
            test_file.unlink()


def test_quarantine_collection():
    """Verify that tests listed in quarantined_tests.txt are marked as quarantine."""
    quarantine_file = Path(__file__).parent / "quarantined_tests.txt"
    backup_content = ""
    if quarantine_file.exists():
        backup_content = quarantine_file.read_text(encoding="utf-8")

    try:
        # Add a dummy test name to quarantine
        quarantine_file.write_text("dummy_quarantined_test\n", encoding="utf-8")

        # We can dynamically run pytest collection or verify using a mock config
        from tests.conftest import pytest_collection_modifyitems

        class MockConfig:
            def __init__(self, rootdir):
                self.rootdir = rootdir

        class MockItem:
            def __init__(self, nodeid, name):
                self.nodeid = nodeid
                self.name = name
                self.markers = []

            def add_marker(self, marker):
                self.markers.append(marker)

        config = MockConfig(Path(__file__).parent.parent)
        item1 = MockItem("tests/test_dummy.py::dummy_quarantined_test", "dummy_quarantined_test")
        item2 = MockItem("tests/test_dummy.py::healthy_test", "healthy_test")

        items = [item1, item2]
        pytest_collection_modifyitems(config, items)

        # Verify item1 got marked as quarantine
        assert len(item1.markers) > 0
        assert item1.markers[0].name == "quarantine"
        # Verify item2 did not
        assert len(item2.markers) == 0

    finally:
        # Restore backup
        if backup_content:
            quarantine_file.write_text(backup_content, encoding="utf-8")
        else:
            if quarantine_file.exists():
                quarantine_file.unlink()
