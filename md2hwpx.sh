#!/bin/sh
# Run md2hwpx in the Docker image (with the diagram tools) from any folder:
#   md2hwpx.sh INPUT [-o OUTPUT] [-t TEMPLATE]
# The current folder is mounted at /work, so INPUT, OUTPUT and TEMPLATE must be inside it.
# This repository's code is mounted read-only; without -t, ./template.hwpx is used if it
# exists, else the template that comes with the code.
set -eu

repo=$(cd "$(dirname "$0")" && pwd)

exec docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD":/work -v "$repo":/md2hwpx:ro -e PYTHONPATH=/md2hwpx/src \
    md2hwpx python -m md2hwpx "$@"
