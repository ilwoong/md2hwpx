#!/bin/sh
# Run md2hwpx in the Docker image (with the diagram tools) from any folder:
#   md2hwpx.sh INPUT [-o OUTPUT] [-t TEMPLATE]
# The current folder is mounted at /work, so INPUT, OUTPUT and TEMPLATE must be inside it.
# This repository's code is mounted read-only; without -t, ./template.hwpx is used if it
# exists, else this repository's template.hwpx.
set -eu

repo=$(cd "$(dirname "$0")" && pwd)

if [ "$#" -gt 0 ] && [ ! -f template.hwpx ]; then
    has_template=no
    for arg in "$@"; do
        case "$arg" in
            -t | --template | --template=*) has_template=yes ;;
        esac
    done
    if [ "$has_template" = no ]; then
        set -- "$@" -t /md2hwpx/template.hwpx
    fi
fi

exec docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD":/work -v "$repo":/md2hwpx:ro -e PYTHONPATH=/md2hwpx/src \
    md2hwpx python -m md2hwpx "$@"
