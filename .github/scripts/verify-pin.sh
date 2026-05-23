#!/usr/bin/env bash
# WOR-559 — deploy gate: assert the worthless version pinned in install.sh
# matches the signed release tag.
#
# Runs AFTER the GPG tag-signature check (.github/scripts/verify-tag.sh) so a
# passing result is bound to a maintainer-signed tag, not an arbitrary ref. A
# mismatch means the served installer would pin a version the signed tag did
# NOT vouch for (or a release bump is half-done) — fail closed rather than
# ship an installer whose default version nobody signed for.
#
# Single source of truth, called from the deploy job. Unit-tested by
# tests/test_deploy_static.py::TestVerifyPinScript.
#
# Inputs (env):
#   TAG_VERSION      required — release version WITHOUT the leading `v`
#                    (deploy passes "${GITHUB_REF_NAME#v}").
#   INSTALL_SH_PATH  optional — path to install.sh (default: install.sh).
set -euo pipefail

install_sh="${INSTALL_SH_PATH:-install.sh}"
tag_version="${TAG_VERSION:-}"

if [ -z "$tag_version" ]; then
  echo "::error title=verify-pin::TAG_VERSION is empty — cannot verify the install.sh pin against the release tag." >&2
  exit 1
fi

if [ ! -f "$install_sh" ]; then
  echo "::error title=verify-pin::install.sh not found at '$install_sh'." >&2
  exit 1
fi

# Extract the WORTHLESS_VERSION_PIN="..." literal (first match only).
pin="$(sed -n 's/^WORTHLESS_VERSION_PIN="\([^"]*\)".*/\1/p' "$install_sh" | head -n1)"

if [ -z "$pin" ]; then
  echo "::error title=verify-pin::install.sh has no non-empty WORTHLESS_VERSION_PIN — refusing to deploy an unpinned installer (would fall back to PyPI latest)." >&2
  exit 1
fi

if [ "$pin" != "$tag_version" ]; then
  echo "::error title=verify-pin::install.sh pin '$pin' != release tag '$tag_version'. Bump WORTHLESS_VERSION_PIN (and pyproject.toml) to match the signed tag before deploying." >&2
  exit 1
fi

echo "OK: install.sh pin '$pin' matches release tag '$tag_version'."
