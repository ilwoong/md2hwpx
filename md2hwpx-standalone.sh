#!/bin/sh
# Convert Markdown to HWPX in the Docker image (with the diagram tools), from any folder:
#   md2hwpx-standalone.sh INPUT [-o OUTPUT] [-t TEMPLATE] [--include-draft] [--include PATTERN ...] [--chapter-start N]
# Copy this one file anywhere and run it; the code, the diagram tools and the built-in template all
# come from the image, so nothing else of the md2hwpx repository is needed beside it. The image is
# always md2hwpx-web:latest (MD2HWPX_IMAGE names another); a src/ folder that happens to sit next
# to the script is not consulted.
# The template is this folder's, not the current one: template.hwpx beside the script (copy it there
# too). Without it, and without -t, the image's own template is used and said on stderr.
# The current folder and the folders of INPUT, OUTPUT and TEMPLATE are mounted at their own paths,
# and the container works in the current folder, so the paths mean what they mean outside it.
# INPUT's images must be inside its folder (a file's folder, or the folder given) or the current one.
set -eu

# Where this script is, even when it was found through PATH (then $0 has no slash) or is a symlink:
# the template is looked for beside the file itself.
case $0 in
    */*) self=$0 ;;
    *) self=$(command -v "$0") ;;
esac
while [ -L "$self" ]; do
    target=$(readlink "$self") || break
    case $target in
        /*) self=$target ;;
        *) self=$(dirname -- "$self")/$target ;;
    esac
done
here=$(cd -P -- "$(dirname -- "$self")" && pwd)
name=${0##*/}
# The code and the tools, always from the image; no local checkout is consulted.
image=${MD2HWPX_IMAGE:-md2hwpx-web:latest}

die() {
    echo "$name: $1" >&2
    exit 1
}

# Before anything is resolved through the image: a missing tag then reads as a missing tag.
docker image inspect "$image" >/dev/null 2>&1 ||
    die "docker image not found: $image
    Build it (build-images.sh) or name another with MD2HWPX_IMAGE."

# The paths among the arguments, as argparse reads them.
input= output= template= tgiven= want= help=
for arg do
    case $want in
        o) output=$arg want=; continue ;;
        t) template=$arg tgiven=1 want=; continue ;;
        v) want=; continue ;;  # the value of an option that names no path: not INPUT
    esac
    case $arg in
        -o|--output) want=o ;;
        --output=*) output=${arg#*=} ;;
        -o?*) output=${arg#-o} ;;
        -t|--template) want=t tgiven=1 ;;
        --template=*) template=${arg#*=} tgiven=1 ;;
        -t?*) template=${arg#-t} tgiven=1 ;;
        --include|--chapter-start) want=v ;;
        -h|--help) help=1 ;;
        -*) ;;
        *) [ -n "$input" ] || input=$arg ;;
    esac
done

# Without INPUT, or with -h, the image prints its help, and help needs no template.
if [ -n "$input" ] && [ -z "$help" ]; then
    if [ -n "$tgiven" ]; then
        # Given, a template that is not a file is an error here: in the container it may be a folder
        # that Docker made, so md2hwpx's message would be about something else.
        [ -z "$template" ] || [ -f "$template" ] || die "template not found: $template"
    else
        if [ -f "$here/template.hwpx" ]; then
            template=$here/template.hwpx
        else
            # Nothing beside the script: the image's own copy, the same file, named in full so that a
            # template.hwpx in the current folder cannot win. md2hwpx-web carries it at /app/src.
            template=/app/src/md2hwpx/template.hwpx
            echo "$name: using the template built into $image" >&2
        fi
        # The default is this folder's, not the image's or the current one's: pass it as given.
        set -- "$@" -t "$template"
    fi
fi

# An OUTPUT ending in "/" is a folder to make. Made here rather than left to md2hwpx: an image older
# than the code makes the folders between inside the container, where they are lost with it.
if [ -n "$input" ]; then
    case $output in
        */) mkdir -p -- "$output" || die "cannot make folder: $output" ;;
    esac
fi

# The folders to mount, one a line, each once. A folder that does not exist is left out, or Docker
# would make it (as root); md2hwpx then says what is missing.
work=$(pwd -P)
if [ "$work" = / ]; then
    # "/" is not a valid bind source or target ("/:/"); the container's own / serves as well.
    mounts=
else
    mounts="$work
"
fi
add() {
    real=$(cd -P -- "$1" 2>/dev/null && pwd -P) || return 0
    [ "$real" != / ] || return 0
    case $mounts in *"
$real
"*|"$real
"*) return 0 ;; esac
    mounts="$mounts$real
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

set -- "$image" python -m md2hwpx "$@"
set -f
IFS='
'
for dir in $mounts; do
    set -- -v "$dir":"$dir" "$@"
done
unset IFS
set +f

set -- --rm --platform linux/amd64 --user "$(id -u):$(id -g)" -w "$work" "$@"

# Without INPUT, or with -h: md2hwpx's help, and below it what is particular to this script.
if [ -z "$input" ] || [ -n "$help" ]; then
    docker run "$@" || exit
    cat <<EOF

예:
  $name docs/ --include 'ch_2*.md' --chapter-start 2

  --include 의 패턴은 따옴표로 감싼다 ('ch_2*.md'). 감싸지 않으면 셸이 현재 폴더에서
  맞는 파일 이름으로 펼쳐 넘겨서, 의도와 다른 파일이 변환되거나 인자 오류가 난다.
EOF
    exit 0
fi

exec docker run "$@"
