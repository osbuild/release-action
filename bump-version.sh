#!/bin/bash

VERSION=$(( $1 + 1 ))

find -name "*osbuild*.spec" -or -name "cockpit-*.spec" \
    | xargs sed -i -E "s/(Version:\\s+)[0-9]+/\1$VERSION/"

if [ -f "setup.py" ]; then
  sed -i -E "s/(version=\")[0-9]+/\1$VERSION/" setup.py
fi

if [ -f "osbuild/__init__.py" ]; then
  sed -i -E "s/(__version__ = \")[0-9]+/\1$VERSION/" osbuild/__init__.py
fi
