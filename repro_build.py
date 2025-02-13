#!/usr/bin/env python3

import argparse
import datetime
import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path


logger = logging.getLogger(__name__)

ENV_RUNTIME = "REPRO_RUNTIME"
ENV_DATETIME = "REPRO_DATETIME"
ENV_SDE = "REPRO_SOURCE_DATE_EPOCH"
ENV_CACHE = "REPRO_CACHE"
ENV_BUILDKIT = "REPRO_BUILDKIT_IMAGE"
ENV_ROOTLESS = "REPRO_ROOTLESS"

DEFAULT_BUILDKIT_IMAGE = "moby/buildkit:v0.19.0@sha256:14aa1b4dd92ea0a4cd03a54d0c6079046ea98cd0c0ae6176bdd7036ba370cbbe"
DEFAULT_BUILDKIT_IMAGE_ROOTLESS = "moby/buildkit:v0.19.0-rootless@sha256:e901cffdad753892a7c3afb8b9972549fca02c73888cf340c91ed801fdd96d71"

MSG_BUILD_CTX = """Build parameters:
- Container runtime: {runtime}
- Buildkit image: {buildkit_image}
- SOURCE_DATE_EPOCH: {sde}
- Rootless support: {rootless}
- Caching enabled: {use_cache}
- Build context: {context}
- Dockerfile: {dockerfile}
- Tag: {tag}
- Buildkit arguments: {buildkit_args}
- Docker Buildx arguments: {buildx_args}
"""


def run(cmd, dry=False, check=True):
    action = "Would have run" if dry else "Running"
    logger.debug(f"{action} : {shlex.join(cmd)}")
    if not dry:
        subprocess.run(cmd, check=check)


def detect_container_runtime() -> str:
    """Auto-detect the installed container runtime in the system."""
    if shutil.which("docker"):
        return "docker"
    elif shutil.which("podman"):
        return "podman"
    else:
        return None


def parse_runtime(args) -> str:
    if args.runtime is not None:
        return args.runtime

    runtime = os.environ.get(ENV_RUNTIME)
    if runtime is None:
        raise RuntimeError("No container runtime detected in your system")
    if runtime not in ("docker", "podman"):
        raise RuntimeError(
            "Only 'docker' or 'podman' container runtimes"
            " are currently supported by this script"
        )


def parse_use_cache(args) -> bool:
    if args.no_cache:
        return False
    return bool(int(os.environ.get(ENV_CACHE, "1")))


def parse_rootless(args, runtime: str) -> bool:
    rootless = args.rootless or bool(int(os.environ.get(ENV_ROOTLESS, "0")))
    if runtime != "podman" and rootless:
        raise RuntimeError("Rootless mode is only supported with Podman runtime")
    return rootless


def parse_sde(args) -> str:
    sde = args.source_date_epoch
    dt = args.datetime

    if (sde is not None and dt is not None) or (sde is None and dt is None):
        raise RuntimeError("You need to pass either a source date epoch or a datetime")

    if sde is not None:
        return str(sde)

    if dt is not None:
        return int(datetime.datetime.fromisoformat(dt).timestamp())


def parse_buildkit_image(args, rootless: bool, runtime: str) -> str:
    default = DEFAULT_BUILDKIT_IMAGE_ROOTLESS if rootless else DEFAULT_BUILDKIT_IMAGE
    img = args.buildkit_image or os.environ.get(ENV_BUILDKIT, default)

    if runtime == "podman" and not img.startswith("docker.io/"):
        img = "docker.io/" + img

    return img


def parse_buildkit_args(args, runtime: str) -> str:
    if not args.buildkit_args:
        return []

    if runtime != "podman":
        raise RuntimeError("Cannot specify Buildkit arguments using the Podman runtime")

    return shlex.split(args.buildkit_args)


def parse_buildx_args(args, runtime: str) -> str:
    if not args.buildx_args:
        return []

    if runtime != "docker":
        raise RuntimeError(
            "Cannot specify Docker Buildx arguments using the Podman runtime"
        )

    return shlex.split(args.buildx_args)


def parse_path(path: str | None) -> str | None:
    return path and str(Path(path).absolute())


def parse_args() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime",
        choices=["docker", "podman"],
        default=detect_container_runtime(),
        help="The container runtime for building the image (default: %(default)s})",
    )
    parser.add_argument(
        "--datetime",
        metavar="YYYY-MM-DD",
        default=None,
        help=(
            "Provide a date and (optionally) a time in ISO format, which will"
            " be used as the timestamp of the image layers"
        ),
    )
    parser.add_argument(
        "--buildkit-image",
        metavar="NAME:TAG@DIGEST",
        default=None,
        help=(
            "The Buildkit container image which will be used for building the"
            " reproducible container image. Make sure to pass the '-rootless'"
            " variant if you are using rootless Podman"
            " (default: docker.io/moby/buildkit:v0.19.0)"
        ),
    )
    parser.add_argument(
        "--source-date-epoch",
        metavar="SECONDS",
        type=int,
        default=None,
        help="Provide a Unix timestamp for the image layers",
    )
    parser.add_argument(
        "--no-cache",
        default=False,
        action="store_true",
        help="Do not use existing cached images for the container build. Build from the start with a new set of cached layers.",
    )
    parser.add_argument(
        "--rootless",
        default=False,
        action="store_true",
        help="Run Buildkit in rootless mode (Podman only)",
    )
    parser.add_argument(
        "-f",
        "--file",
        metavar="FILE",
        default=None,
        help="Pathname of a Dockerfile",
    )
    parser.add_argument(
        "-t",
        "--tag",
        metavar="TAG",
        default=None,
        help="Tag the built image with the name %(metavar)s",
    )
    parser.add_argument(
        "--buildkit-args",
        metavar="'ARG1 ARG2'",
        default=None,
        help="Extra arguments for Buildkit (Podman only)",
    )
    parser.add_argument(
        "--buildx-args",
        metavar="'ARG1 ARG2'",
        default=None,
        help="Extra arguments for Docker Buildx (Docker only)",
    )
    parser.add_argument(
        "--dry",
        default=False,
        action="store_true",
        help="Do not run any commands, just print what would happen",
    )
    parser.add_argument(
        "context",
        metavar="CONTEXT",
        help="Path to the build context",
    )
    return parser.parse_args()


def podman_build(
    context: str,
    dockerfile: str | None,
    tag: str | None,
    buildkit_image: str,
    sde: int,
    rootless: bool,
    use_cache: bool,
    buildkit_args: list,
    dry: bool,
):
    rootless_args = []
    rootful_args = []
    if rootless:
        rootless_args = [
            "--userns",
            "keep-id:uid=1000,gid=1000",
            "--security-opt",
            "seccomp=unconfined",
            "--security-opt",
            "apparmor=unconfined",
            "-e",
            "BUILDKITD_FLAGS=--oci-worker-no-process-sandbox",
        ]
    else:
        rootful_args = ["--privileged"]

    dockerfile_args_podman = []
    dockerfile_args_buildkit = []
    if dockerfile:
        dockerfile_args_podman = ["-v", f"{dockerfile}:/tmp/Dockerfile"]
        dockerfile_args_buildkit = ["--local", "dockerfile=/tmp"]

    tag_args = f",name={tag}" if tag else ""

    cache_args = []
    if use_cache:
        cache_args = [
            "--export-cache",
            "type=local,dest=/tmp/cache",
            "--import-cache",
            "type=local,src=/tmp/cache",
        ]

    # TODO: Add cache args, and cache dir
    with tempfile.TemporaryDirectory() as d:
        cmd = [
            "podman",
            "run",
            "-it",
            "--rm",
            "-v",
            "buildkit_cache:/tmp/cache",
            "-v",
            f"{d}:/tmp/image",
            "-v",
            f"{context}:/tmp/work",
            "--entrypoint",
            "buildctl-daemonless.sh",
            *rootless_args,
            *rootful_args,
            *dockerfile_args_podman,
            buildkit_image,
            "build",
            "--frontend",
            "dockerfile.v0",
            "--local",
            "context=/tmp/work",
            "--opt",
            f"build-arg:SOURCE_DATE_EPOCH={sde}",
            "--output",
            f"type=oci,dest=/tmp/image/image.tar,rewrite_timestamp=true{tag_args}",
            *cache_args,
            *dockerfile_args_buildkit,
            *buildkit_args,
        ]

        run(cmd, dry)
        run(["podman", "load", "-i", str(Path(d) / "image.tar")])


def docker_build(
    context: str,
    dockerfile: str | None,
    tag: str | None,
    buildkit_image: str,
    sde: int,
    use_cache: bool,
    buildx_args: list,
    dry: bool,
):
    h = hashlib.sha256()
    h.update(buildkit_image.encode())
    builder_id = h.hexdigest()
    builder_name = f"repro-build-{builder_id}"
    tag_args = f",name={tag}" if tag else ""
    cache_args = [] if use_cache else ["--no-cache", "--pull"]

    cmd = [
        "docker",
        "buildx",
        "create",
        "--name",
        builder_name,
        "--driver-opt",
        f"image={buildkit_image}",
    ]
    run(cmd, dry, check=False)

    dockerfile_args = ["-f", dockerfile] if dockerfile else []

    cmd = [
        "docker",
        "buildx",
        "--builder",
        builder_name,
        "build",
        "--build-arg",
        f"SOURCE_DATE_EPOCH={sde}",
        "--provenance",
        "false",
        "--output",
        f"type=image,rewrite_timestamp=true{tag_args}",
        *cache_args,
        *dockerfile_args,
        *buildx_args,
        context,
    ]
    run(cmd, dry)


def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()

    runtime = parse_runtime(args)
    use_cache = parse_use_cache(args)
    sde = parse_sde(args)
    rootless = parse_rootless(args, runtime)
    buildkit_image = parse_buildkit_image(args, rootless, runtime)
    buildkit_args = parse_buildkit_args(args, runtime)
    buildx_args = parse_buildx_args(args, runtime)
    tag = args.tag
    dockerfile = parse_path(args.file)
    dry = args.dry
    context = parse_path(args.context)

    logger.info(
        MSG_BUILD_CTX.format(
            runtime=runtime,
            buildkit_image=buildkit_image,
            sde=sde,
            rootless=rootless,
            use_cache=use_cache,
            context=context,
            dockerfile=dockerfile or "(not provided)",
            tag=tag or "(not provided)",
            buildkit_args=" ".join(buildkit_args) or "(not provided)",
            buildx_args=" ".join(buildx_args) or "(not provided)",
        )
    )

    try:
        if runtime == "docker":
            return docker_build(
                context,
                dockerfile,
                tag,
                buildkit_image,
                sde,
                use_cache,
                buildx_args,
                dry,
            )
        else:
            return podman_build(
                context,
                dockerfile,
                tag,
                buildkit_image,
                sde,
                rootless,
                use_cache,
                buildkit_args,
                dry,
            )
    except subprocess.CalledProcessError as e:
        print(f"Failed with {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    sys.exit(main())
