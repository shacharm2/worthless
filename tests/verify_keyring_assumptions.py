"""
WOR-185 Assumption Verification — run BEFORE implementation.
Tests keyring behavior on the current platform.
"""

import os
import subprocess
import sys
import textwrap


def header(name: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")


SERVICE = "worthless-test"
KEY = "fernet-key"
VALUE = "dGVzdC1mZXJuZXQta2V5LWJhc2U2NA=="  # base64-ish test value


def a1_backend_detection():
    """A1: Does keyring auto-detect the right backend?"""
    header("A1: Backend Detection")
    import keyring

    backend = keyring.get_keyring()
    name = type(backend).__name__
    print(f"Backend class: {name}")
    print(f"Backend repr:  {backend}")

    # Reject insecure backends (check full module path, not just class name)
    module = type(backend).__module__
    full_name = f"{module}.{name}"
    print(f"Full path:     {full_name}")
    insecure_modules = ("keyring.backends.fail", "keyring.backends.null", "keyrings.alt.file")
    if any(full_name.startswith(m) for m in insecure_modules) or name == "PlaintextKeyring":
        print(f"FAIL: insecure or null backend detected: {name}")
        return False
    print("PASS: usable backend detected")
    return True


def a2_no_repeated_prompt():
    """A2: Does get_password() work without prompts after initial set?"""
    header("A2: No Repeated Prompts")
    import keyring

    print("Setting test value (may prompt on macOS — that's OK)...")
    keyring.set_password(SERVICE, KEY, VALUE)
    print("Set succeeded.")

    print("Reading 5 times (should NOT prompt)...")
    for i in range(5):
        val = keyring.get_password(SERVICE, KEY)
        if val != VALUE:
            print(f"FAIL: read {i} returned {val!r}, expected {VALUE!r}")
            return False
        print(f"  Read {i}: OK")

    print("PASS: 5 reads without prompt")
    return True


def a3_subprocess_access():
    """A3: Can a child subprocess read keyring entries set by parent?"""
    header("A3: Subprocess Access")
    import keyring

    # Ensure value is set
    keyring.set_password(SERVICE, KEY, VALUE)

    child_code = textwrap.dedent(f"""
        import keyring
        val = keyring.get_password("{SERVICE}", "{KEY}")
        expected = "{VALUE}"
        if val == expected:
            print("CHILD_PASS")
        else:
            print(f"CHILD_FAIL: got {{val!r}}, expected {{expected!r}}")
    """)

    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    stdout = result.stdout.strip()
    print(f"Child stdout: {stdout}")
    if result.stderr.strip():
        print(f"Child stderr: {result.stderr.strip()}")

    if "CHILD_PASS" in stdout:
        print("PASS: subprocess can read parent's keyring entry")
        return True
    print("FAIL: subprocess cannot read keyring entry")
    return False


def a4_no_backend_behavior():
    """A4: What happens when keyring has no backend? (informational)"""
    header("A4: No-Backend Behavior (informational)")
    import keyring

    backend = keyring.get_keyring()
    name = type(backend).__name__
    print(f"Current backend: {name}")
    print("(On headless/Docker this would be 'fail.Keyring' or similar)")
    print("This test is informational on desktop — run in Docker to verify failure mode.")

    # Test what None return looks like
    val = keyring.get_password(SERVICE, "nonexistent-key")
    print(f"get_password for nonexistent key returns: {val!r} (type: {type(val).__name__})")
    if val is None:
        print("PASS: nonexistent key returns None (not exception)")
    return True


def a8_string_vs_bytes():
    """A8: get_password returns str, verify encode() roundtrip."""
    header("A8: String vs Bytes Roundtrip")
    import keyring

    keyring.set_password(SERVICE, KEY, VALUE)
    val = keyring.get_password(SERVICE, KEY)

    print(f"Type returned: {type(val).__name__}")
    print(f"Value: {val!r}")

    # Verify roundtrip
    as_bytes = val.encode("utf-8")
    back_to_str = as_bytes.decode("utf-8")
    print(f"encode() -> {type(as_bytes).__name__}, decode() -> {back_to_str!r}")

    if back_to_str == VALUE:
        print("PASS: str/bytes roundtrip works")
        return True
    print("FAIL: roundtrip mismatch")
    return False


def a7_dependency_footprint():
    """A7: Check keyring dependency tree."""
    header("A7: Dependency Footprint")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "keyring"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith(("Name:", "Version:", "Requires:", "Location:")):
            print(f"  {line}")
    print("PASS: informational")
    return True


def cleanup():
    """Remove test keyring entries."""
    header("Cleanup")
    import keyring

    try:
        keyring.delete_password(SERVICE, KEY)
        print("Cleaned up test entry")
    except Exception as e:
        print(f"Cleanup note: {e}")


def main():
    print("WOR-185 Keyring Assumption Verification")
    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.version}")

    results = {}
    tests = [
        ("A1", a1_backend_detection),
        ("A7", a7_dependency_footprint),
        ("A2", a2_no_repeated_prompt),
        ("A3", a3_subprocess_access),
        ("A4", a4_no_backend_behavior),
        ("A8", a8_string_vs_bytes),
    ]

    for name, test_fn in tests:
        try:
            results[name] = test_fn()
        except Exception as e:
            print(f"EXCEPTION: {type(e).__name__}: {e}")
            results[name] = False

    cleanup()

    header("SUMMARY")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")

    all_pass = all(results.values())
    print(f"\nOverall: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
