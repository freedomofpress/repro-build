#!/bin/bash

set -x

CACHE_DIR=~/.local/share/dangerzone-dev/buildkit/cache
IMAGE_DIR=~/.local/share/dangerzone-dev/buildkit/image
DATE=20250101

mkdir -p ${CACHE_DIR?}
mkdir -p ${IMAGE_DIR?}

podman run \
    -it \
    --rm \
    --userns keep-id \
    -v ${CACHE_DIR?}:/tmp/cache \
    -v ${IMAGE_DIR?}:/tmp/image \
    -v ${1?}:/tmp/work \
    --entrypoint buildctl-daemonless.sh \
    -e BUILDKITD_FLAGS=--oci-worker-no-process-sandbox \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    docker.io/moby/buildkit:v0.19.0-rootless@sha256:e901cffdad753892a7c3afb8b9972549fca02c73888cf340c91ed801fdd96d71 \
    build \
    --frontend \
    dockerfile.v0 \
    --local context=/tmp/work/dangerzone \
    --local dockerfile=/tmp/work \
    --opt build-arg:SOURCE_DATE_EPOCH=$(date -d "${DATE?}Z" +%s) \
    --opt platform=linux/amd64,linux/arm64 \
    --output type=oci,name=dangerzone.rocks/dangerzone/v2:${DATE},dest=/tmp/image/dz-${DATE?}.tar,rewrite-timestamp=true \
    --export-cache type=local,dest=/tmp/cache \
    --import-cache type=local,src=/tmp/cache \

    #docker.io/moby/buildkit:v0.19.0@sha256:14aa1b4dd92ea0a4cd03a54d0c6079046ea98cd0c0ae6176bdd7036ba370cbbe \
