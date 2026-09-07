#!/usr/bin/env bash
# Milestone-1 checks: hardware-free tests + syntax gates.
# QML/manifest gates activate when those trees exist (milestones 3-4).
set -u

fail=0
step() { printf '\n==> %s\n' "$1"; }

step "python unit tests"
if ! PYTHONPATH=backend python3 -m unittest discover -s tests -p 'test_*.py'; then
  fail=1
fi

step "python compileall"
if ! python3 -m compileall -q backend scripts tests; then
  fail=1
fi

step "shell syntax"
for script in scripts/*.sh; do
  [ -e "$script" ] || continue
  if ! bash -n "$script"; then
    fail=1
  fi
done

step "udev rule syntax"
if ! udevadm verify udev/70-op1we-control.rules >/dev/null 2>&1; then
  echo "udev rule verification failed" >&2
  fail=1
fi

if [ -f manifest.json ]; then
  step "plugin manifest validation"
  if ! omarchy plugin validate .; then
    fail=1
  fi
else
  echo "(skip) no manifest.json yet (milestone 3+)"
fi

if ls qml/*.qml >/dev/null 2>&1; then
  step "QML lint"
  # qs.Ui/qs.Commons live directly under the shell dir, so qmllint needs a
  # mapping root with the qs/ prefix (verified against installed shell in
  # milestone 3). Built in /tmp: symlinks are forbidden inside the plugin.
  lint_root=$(mktemp -d)
  mkdir -p "$lint_root/qs"
  ln -s /usr/share/omarchy/shell/Ui "$lint_root/qs/Ui"
  ln -s /usr/share/omarchy/shell/Commons "$lint_root/qs/Commons"
  if ! /usr/lib/qt6/bin/qmllint -I "$lint_root" -I /usr/lib/qt6/qml qml/*.qml; then
    fail=1
  fi
  rm -rf "$lint_root"
else
  echo "(skip) no qml/ yet (milestone 3)"
fi

if [ -f tests/model.test.cjs ]; then
  step "node model tests"
  if ! node --test tests/model.test.cjs; then
    fail=1
  fi
else
  echo "(skip) no tests/model.test.cjs yet (milestone 3)"
fi

if [ "$fail" -ne 0 ]; then
  echo "check.sh: FAILURES" >&2
  exit 1
fi
echo "check.sh: all applicable checks passed"
