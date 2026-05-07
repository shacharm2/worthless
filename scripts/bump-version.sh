#!/bin/sh
# bump-version.sh — bump worthless's version in BOTH the places that need it
# atomically. Use BEFORE tagging a release.
#
# v0.3.4 shipped with a CI failure because pyproject.toml was bumped but
# SKILL.md wasn't. tests/test_skill_md.py::TestVersionDrift catches this,
# but only AFTER the broken commit lands. This script makes the bump
# happen in one step so the test never has to fail.
#
# Usage:
#     ./scripts/bump-version.sh 0.3.5
#     ./scripts/bump-version.sh 1.0.0
#
# What it does:
#   1. Validates the version arg (PEP 440-ish)
#   2. Updates pyproject.toml `version = "X.Y.Z"`
#   3. Updates SKILL.md `**Version**: X.Y.Z`
#   4. Adds a `[X.Y.Z]: https://github.com/...releases/tag/vX.Y.Z` link
#      reference at the bottom of CHANGELOG.md (matches the existing
#      pattern). Does NOT touch the body of CHANGELOG — you still write
#      the release notes by hand or via a future release-please bot.
#   5. Runs `uv sync` so the venv reflects the new version
#   6. Prints a checklist of what to do next
#
# Idempotent: re-running with the same version is a no-op.
# Safe: makes no commits and no tags. You stage and commit yourself.

set -eu

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

# --- 1. Validate input -------------------------------------------------------

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 0.3.5"
    exit 1
fi

new_version="$1"

# PEP 440-ish: digits, dots, optional pre-release suffix
case "$new_version" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *)
        echo "ERROR: '$new_version' doesn't look like a semver/PEP-440 version."
        echo "Examples: 0.3.5 / 1.0.0 / 0.4.0rc1"
        exit 1
        ;;
esac

# --- 2. Discover current version --------------------------------------------

current_version=$(awk -F'"' '/^version =/ { print $2; exit }' pyproject.toml)
if [ -z "$current_version" ]; then
    echo "ERROR: could not parse current version from pyproject.toml"
    exit 1
fi

if [ "$current_version" = "$new_version" ]; then
    echo "Already at version $new_version. Nothing to do."
    exit 0
fi

echo "Bumping: $current_version -> $new_version"
echo

# --- 3. Update pyproject.toml -----------------------------------------------

# Use a tempfile + mv for atomic replace (BSD/GNU sed both work)
sed -E "s|^version = \"$current_version\"|version = \"$new_version\"|" pyproject.toml > pyproject.toml.tmp
mv pyproject.toml.tmp pyproject.toml
echo "  ✓ pyproject.toml: version = \"$new_version\""

# --- 4. Update SKILL.md -----------------------------------------------------

sed -E "s|^- \*\*Version\*\*: $current_version|- **Version**: $new_version|" SKILL.md > SKILL.md.tmp
mv SKILL.md.tmp SKILL.md
echo "  ✓ SKILL.md: **Version**: $new_version"

# --- 5. Append CHANGELOG.md link reference ----------------------------------

if grep -q "^\[$new_version\]:" CHANGELOG.md; then
    echo "  ✓ CHANGELOG.md: [$new_version] link reference already present"
else
    # Insert AFTER the [Unreleased] line if present, otherwise before [previous]
    new_link="[$new_version]: https://github.com/shacharm2/worthless/releases/tag/v$new_version"

    # Find the first existing [X.Y.Z]: link and insert above it
    if grep -q "^\[$current_version\]:" CHANGELOG.md; then
        # Insert the new link just before the current-version link
        sed -E "/^\[$current_version\]: /i\\
$new_link
" CHANGELOG.md > CHANGELOG.md.tmp
        mv CHANGELOG.md.tmp CHANGELOG.md
        echo "  ✓ CHANGELOG.md: added [$new_version] reference link"
    else
        echo "  ⚠ CHANGELOG.md: no existing [$current_version] reference link to anchor against."
        echo "    Appended [$new_version] link at the end. Verify position manually."
        printf '\n%s\n' "$new_link" >> CHANGELOG.md
    fi
fi

# --- 6. Re-sync uv so venv matches ------------------------------------------

if command -v uv >/dev/null 2>&1; then
    echo
    echo "Re-syncing uv ..."
    uv sync --reinstall-package worthless 2>&1 | tail -2
else
    echo "  ⚠ uv not on PATH — skipping uv sync. Re-sync manually before tagging."
fi

# --- 7. Tell the user what's left -------------------------------------------

cat <<EOF

Done. Next steps:

  1. Add a CHANGELOG body for $new_version under '## [Unreleased]'.
     (Convert the existing '## [Unreleased]' header to '## [$new_version] — $(date +%Y-%m-%d)'
     and add a fresh empty '## [Unreleased]' above it.)

  2. Verify:
     git diff pyproject.toml SKILL.md CHANGELOG.md

  3. Run the version-drift test:
     uv run pytest tests/test_skill_md.py::TestVersionDrift -q

  4. Stage, commit (Conventional Commits), push:
     git add pyproject.toml SKILL.md CHANGELOG.md uv.lock
     git commit -m "chore(release): v$new_version"
     git push

  5. After PR merges to main, tag and release:
     gh release create v$new_version --generate-notes --title "v$new_version: <headline>"
     # publish.yml fires automatically on the v* tag → PyPI

EOF
