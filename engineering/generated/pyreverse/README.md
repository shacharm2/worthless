# Pyreverse Output

This directory contains deterministic structure artifacts produced from the current Python package layout via `pyreverse`.

Current outputs:

- `packages_worthless.dot`: package dependency and containment view
- `classes_worthless.dot`: class relationship view

Use these artifacts to:

- verify package/class structure claims in generated docs
- detect drift between code layout and engineering documentation
- ground future AI-generated updates in real structure
