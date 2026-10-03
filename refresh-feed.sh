#!/usr/bin/env bash
# Both workflows must hold the same repository-wide concurrency lock.
set -euo pipefail
mode="${1:-collect}"
[[ "$mode" == collect || "$mode" == watchdog ]]
[[ -z "$(git status --porcelain)" ]] || { echo 'Checkout must be clean'; exit 1; }
git pull --ff-only origin main
if [[ "$mode" == watchdog ]] && python verify_snapshot.py; then
  echo 'Snapshot is healthy; rescue skipped.'
  exit 0
fi
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# Bounded retries cover transient exchange errors and collection across an hour boundary.
ready=false
for attempt in 1 2 3; do
  if python collector.py --output "$stage" && python verify_snapshot.py --data-dir "$stage"; then
    ready=true
    break
  fi
  if [[ "$attempt" != 3 ]]; then sleep 5; fi
done
if [[ "$ready" != true ]]; then
  echo 'No valid snapshot; existing data and history preserved.'
  exit 1
fi
for asset in BTC ETH SOL XRP DOGE BNB; do
  cp "$stage/$asset.json" "$stage/$asset.md" data/
done
cp "$stage/status.json" "$stage/README.md" data/
python verify_snapshot.py
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add data/BTC.* data/ETH.* data/SOL.* data/XRP.* data/DOGE.* data/BNB.* data/status.json data/README.md
if git diff --cached --quiet; then exit 0; fi
git commit -m "chore: refresh public OHLCV snapshots ($mode)"
# Never force-push or resolve data conflicts by overwriting a newer snapshot.
# Unrelated changes can rebase safely; data conflicts fail the run visibly.
git pull --rebase origin main
python verify_snapshot.py
git push origin HEAD:main
# Verify the actual published tip, including any intervening writer.
git fetch origin main
published=$(mktemp -d)
git archive origin/main data | tar -x -C "$published"
python verify_snapshot.py --data-dir "$published/data"
rm -rf "$published"
