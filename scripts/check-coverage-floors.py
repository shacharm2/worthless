#!/usr/bin/env python3
"""Check per-module coverage floors from coverage.xml."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FLOORS = {
    "worthless.crypto": 95.0,
    "worthless.proxy": 85.0,
}
OVERALL_FLOOR = 80.0


def main() -> int:
    coverage_path = Path("coverage.xml")
    if not coverage_path.exists():
        print(f"ERROR: {coverage_path} not found. Run pytest --cov first.")
        return 1

    try:
        tree = ET.parse(coverage_path)  # noqa: S314 — trusted coverage XML we generated
    except ET.ParseError as exc:
        print(f"ERROR: failed to parse {coverage_path}: {exc}")
        return 1

    root = tree.getroot()

    # Check overall
    overall = float(root.get("line-rate", 0)) * 100
    failed = False
    if overall < OVERALL_FLOOR:
        print(f"FAIL: overall coverage {overall:.1f}% < {OVERALL_FLOOR}%")
        failed = True
    else:
        print(f"OK: overall coverage {overall:.1f}% >= {OVERALL_FLOOR}%")

    # Check per-module floors
    seen_modules: set[str] = set()
    for pkg in root.findall(".//package"):
        name = pkg.get("name", "").replace("/", ".").removeprefix("src.")
        rate = float(pkg.get("line-rate", 0)) * 100
        for module, floor in FLOORS.items():
            if name == module or name.startswith(module + "."):
                seen_modules.add(module)
                if rate < floor:
                    print(f"FAIL: {name} coverage {rate:.1f}% < {floor}%")
                    failed = True
                else:
                    print(f"OK: {name} coverage {rate:.1f}% >= {floor}%")

    # Check that all floor modules were found in coverage data
    missing = set(FLOORS) - seen_modules
    if missing:
        for module in sorted(missing):
            print(f"FAIL: floor module {module!r} not found in coverage data")
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
