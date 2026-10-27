#!/usr/bin/env bash
# Render ha-app/ for one release and push it to the ha-app branch, which is the
# app repository Home Assistant reads (https://github.com/<owner>/<repo>#ha-app).
#
# Usage: publish-ha-app.sh <version> <image> <repo-url> <release-note>
# Run only after <image>:<version> has been pushed to the registry, otherwise
# Home Assistant would offer an update it cannot pull.
set -euo pipefail

version=$1
image=$2
repo_url=$3
note=$4
branch=ha-app
config=energie_dashboard/config.yaml
worktree=$(mktemp -d)

if git ls-remote --exit-code --heads origin "$branch" >/dev/null; then
  git fetch --depth=1 origin "$branch"
  git worktree add --detach "$worktree" FETCH_HEAD
  current=$(tr -d '\r' <"$worktree/$config" | sed -n 's/^version: "\(.*\)"$/\1/p')
  # The last version segment is the workflow run number. Never go backwards,
  # e.g. when an old workflow run is re-run. (ci.yml checks this before
  # building already; this is the last line of defence.)
  if [[ -n "$current" && "${current##*.}" -ge "${version##*.}" ]]; then
    echo "::warning title=Nicht veröffentlicht::$branch hat bereits $current, $version wird nicht veröffentlicht."
    git worktree remove --force "$worktree"
    exit 0
  fi
  find "$worktree" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
else
  git worktree add --detach "$worktree"
  git -C "$worktree" checkout --quiet --orphan "$branch"
  git -C "$worktree" rm -rf --quiet .
fi

cp -a ha-app/. "$worktree"/
sed -i \
  -e "s|__VERSION__|$version|g" \
  -e "s|__IMAGE__|$image|g" \
  -e "s|__REPO_URL__|$repo_url|g" \
  "$worktree/repository.yaml" "$worktree/$config"
printf '# Änderungen\n\n## %s\n\n- %s\n' "$version" "$note" >"$worktree/energie_dashboard/CHANGELOG.md"

git -C "$worktree" add -A
git -C "$worktree" \
  -c user.name="github-actions[bot]" \
  -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit --quiet -m "Release $version"
git -C "$worktree" push origin "HEAD:refs/heads/$branch"
git worktree remove --force "$worktree"
