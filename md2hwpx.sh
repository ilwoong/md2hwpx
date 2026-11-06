#!/bin/sh
# Run md2hwpx in the Docker image (with the diagram tools) from any folder:
#   md2hwpx.sh INPUT [-o OUTPUT] [-t TEMPLATE]
# The current folder and the folders of INPUT, OUTPUT and TEMPLATE are mounted at their own paths,
# and the container works in the current folder, so the paths mean what they mean outside it.
# INPUT's images must be inside its folder (a file's folder, or the folder given) or the current one.
# This repository's code is mounted read-only; without -t, ./template.hwpx is used if it
# exists, else the template that comes with the code.
set -eu

repo=$(cd "$(dirname "$0")" && pwd)

# The paths among the arguments, as argparse reads them.
input= output= template= want=
for arg do
    case $want in
        o) output=$arg want=; continue ;;
        t) template=$arg want=; continue ;;
    esac
    case $arg in
        -o|--output) want=o ;;
        --output=*) output=${arg#*=} ;;
        -o?*) output=${arg#-o} ;;
        -t|--template) want=t ;;
        --template=*) template=${arg#*=} ;;
        -t?*) template=${arg#-t} ;;
        -*) ;;
        *) [ -n "$input" ] || input=$arg ;;
    esac
done

# The folders to mount, one a line, each once. A folder that does not exist is left out, or Docker
# would make it (as root); md2hwpx then says what is missing.
work=$(pwd -P)
mounts="$work
"
add() {
    dir=$(cd -P -- "$1" 2>/dev/null && pwd -P) || return 0
    case $mounts in *"
$dir
"*|"$dir
"*) return 0 ;; esac
    mounts="$mounts$dir
"
}
folder() {  # $1 itself if it is a folder, else the folder it is in
    if [ -d "$1" ]; then printf '%s' "$1"; else dirname -- "$1"; fi
}
[ -z "$input" ] || add "$(folder "$input")"
if [ -n "$output" ]; then
    # Its nearest folder that is there: an OUTPUT ending in "/" is made in it, with the folders between,
    # which would otherwise go in the container and be lost with it.
    dir=$(folder "$output")
    while [ ! -d "$dir" ]; do dir=$(dirname -- "$dir"); done
    add "$dir"
fi
[ -z "$template" ] || add "$(dirname -- "$template")"

set -- md2hwpx python -m md2hwpx "$@"
set -f
IFS='
'
for dir in $mounts; do
    set -- -v "$dir":"$dir" "$@"
done
unset IFS
set +f

exec docker run --rm --user "$(id -u):$(id -g)" -w "$work" \
    -v "$repo":/md2hwpx:ro -e PYTHONPATH=/md2hwpx/src "$@"
