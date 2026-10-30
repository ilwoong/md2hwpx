#!/bin/sh
# Build the Docker images, the development one (md2hwpx, see Dockerfile) and the web service one
# (md2hwpx-web, see compose.yaml), and tag each with today's date besides latest:
#   build-images.sh
# The date tag (YYMMDD, e.g. 261030) keeps that day's images when a later build moves latest on, so they can
# be gone back to. Building again on the same day moves that day's tag along with latest.
set -eu

cd "$(dirname "$0")"
tag=$(date +%y%m%d)
docker build -t md2hwpx .
docker compose build web
for image in md2hwpx md2hwpx-web; do
    docker tag "$image:latest" "$image:$tag"
    echo "$image:latest $image:$tag"
done
