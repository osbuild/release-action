#!/bin/bash

if [ -z "$1" ]; then
  echo "Usage: $0 <version>"
  exit 1
fi

if [[ "$1" =~ \. ]]; then
  # If there is a "dot" in the version number, we need to bump the last number
  VERSION=$(echo "$1" | sed -E 's/(.*\.)([0-9]+)$/echo "\1$((\2+1))"/e')
else
  # If there is no "dot" in the version number, we simply increment the version number
  VERSION=$(( $1 + 1 ))
fi


find -name "*osbuild*.spec" -or -name "cockpit-*.spec" -or -name "image-builder*.spec" \
    | xargs sed -i -E "s/(Version:\\s+)[0-9]+/\1$VERSION/"

if [ -f "setup.py" ]; then
  sed -i -E "s/(version=\")[0-9]+/\1$VERSION/" setup.py
fi

if [ -f "osbuild/__init__.py" ]; then
  sed -i -E "s/(__version__ = \")[0-9]+/\1$VERSION/" osbuild/__init__.py
fi
