#!/usr/bin/env python3
"""Check per-module coverage floors from coverage.xml."""
import sys
import xml.etree.ElementTree as ET

FLOORS = {
    "worthless.crypto": 95.0,
    "worthless.proxy": 85.0,
}
OVERALL_FLOOR = 80.0


def main() -> int:
    tree = ET.parse("coverage.xml")
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
    for pkg in root.findall(".//package"):
        name = pkg.get("name", "").replace("/", ".")
        rate = float(pkg.get("line-rate", 0)) * 100
        for module, floor in FLOORS.items():
            if module in name:
                if rate < floor:
                    print(f"FAIL: {name} coverage {rate:.1f}% < {floor}%")
                    failed = True
                else:
                    print(f"OK: {name} coverage {rate:.1f}% >= {floor}%")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
