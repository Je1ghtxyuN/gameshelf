#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "${GAMESHELF_TEST_SPARKLE:-}" ]]; then
 export GAMESHELF_TEST_SPARKLE="$(python3 Tools/fetch_sparkle.py)"
fi
[[ -d "$GAMESHELF_TEST_SPARKLE/Sparkle.framework" ]] || { echo 'Sparkle.framework not found' >&2; exit 1; }
# Every runner creates its own temporary data root and loopback fixture server.
# No installed client, actual game, account or NAS is used.
python3 Tests/run_account_library_integration.py
python3 Tests/run_account_discovery_ui.py
python3 Tests/run_account_legacy_registration.py
python3 Tests/run_mac_integration_review.py
