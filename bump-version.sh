#!/bin/bash

VERSION=$(( $1 + 1 ))

# Enable nullglob to avoid errors on unmatched patterns
# we expect _either_ *osbuild* or *image-builder*
shopt -s nullglob
sed -i -E "s/(Version:\\s+)[0-9]+/\1$VERSION/" *osbuild*.spec *image-builder*.spec

# Disable nullglob to restore default behavior
shopt -u nullglob

if [ -f "setup.py" ]; then
  sed -i -E "s/(version=\")[0-9]+/\1$VERSION/" setup.py
fi

if [ -f "osbuild/__init__.py" ]; then
  sed -i -E "s/(__version__ = \")[0-9]+/\1$VERSION/" osbuild/__init__.py
fi
