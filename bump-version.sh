#!/bin/bash

if [ -z "$1" ]; then
  echo "Usage: $0 <version> [major|minor|patch]"
  exit 1
fi

CURRENT="$1"
BUMP_TYPE="${2:-}"

NEW_MAJOR=0
if [ -n "$BUMP_TYPE" ]; then
  IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
  MINOR="${MINOR:-0}"
  PATCH="${PATCH:-0}"

  case "$BUMP_TYPE" in
    major)
      NEW_MAJOR=$((MAJOR+1))
      VERSION="${NEW_MAJOR}.0.0"
      ;;
    minor)
      VERSION="${MAJOR}.$((MINOR + 1)).0"
      ;;
    patch)
      VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
      ;;
    *)
      echo "Error: bump type must be 'major', 'minor', or 'patch'"
      exit 1
      ;;
  esac
elif [[ "$CURRENT" =~ \. ]]; then
  VERSION=$(echo "$CURRENT" | sed -E 's/(.*\.)([0-9]+)$/echo "\1$((\2+1))"/e')
else
  VERSION=$(( CURRENT + 1 ))
fi


find -name "*osbuild*.spec" -or -name "cockpit-*.spec" -or -name "image-builder*.spec" \
    | xargs sed -i -E "s/(Version:\\s+)[0-9]+[0-9.]*/\1$VERSION/"

if [ -f "setup.py" ]; then
  sed -i -E "s/(version=\")[0-9]+[0-9.]*/\1$VERSION/" setup.py
fi

if [ -f "osbuild/__init__.py" ]; then
  sed -i -E "s/(__version__ = \")[0-9]+[0-9.]*/\1$VERSION/" osbuild/__init__.py
fi


if [ -f "go.mod" ] && (( NEW_MAJOR > 1 )); then
    # update the go module name to match the major version
    module="$(go list -m)"
    # slice off version if it already has one
    module="$(sed -E 's/\/v[0-9]+$//' <<< "${module}")"

    go mod edit -module "${module}/v${NEW_MAJOR}"
fi
