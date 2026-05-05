import argparse
import datetime
import hashlib
import json
import logging
import os
import pprint
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

logger = logging.getLogger(__name__)

MEDIA_TYPE_INDEX_V1_JSON = "application/vnd.oci.image.index.v1+json"
MEDIA_TYPE_MANIFEST_V1_JSON = "application/vnd.oci.image.manifest.v1+json"

ENV_RUNTIME = "REPRO_RUNTIME"
ENV_DATETIME = "REPRO_DATETIME"
ENV_SDE = "REPRO_SOURCE_DATE_EPOCH"
ENV_CACHE = "REPRO_CACHE"
ENV_BUILDKIT = "REPRO_BUILDKIT_IMAGE"
ENV_ROOTLESS = "REPRO_ROOTLESS"

DEFAULT_BUILDKIT_IMAGE = "moby/buildkit:v0.19.0@sha256:14aa1b4dd92ea0a4cd03a54d0c6079046ea98cd0c0ae6176bdd7036ba370cbbe"
DEFAULT_BUILDKIT_IMAGE_ROOTLESS = "moby/buildkit:v0.19.0-rootless@sha256:e901cffdad753892a7c3afb8b9972549fca02c73888cf340c91ed801fdd96d71"

MSG_BUILD_CTX = """Build environment:
- Container runtime: {runtime}
- BuildKit image: {buildkit_image}
- Rootless support: {rootless}
- Caching enabled: {use_cache}
- Build context: {context}
- Dockerfile: {dockerfile}
- Output: {output}

Build parameters:
- SOURCE_DATE_EPOCH: {sde}
- Build args: {build_args}
- Annotations: {annotations}
- Tag: {tag}
- Platform: {platform}

Podman-only arguments:
- BuildKit arguments: {buildkit_args}

Docker-only arguments:
- Docker Buildx arguments: {buildx_args}
"""


def pretty_error(obj: dict, msg: str):
    raise Exception(f"{msg}\n{pprint.pprint(obj)}")


def get_key(obj: dict, key: str) -> object:
    if key not in obj:
        pretty_error(f"Could not find key '{key}' in the dictionary:", obj)
    return obj[key]


def run(cmd, dry=False, check=True):
    action = "Would have run" if dry else "Running"
    logger.debug(f"{action}: {shlex.join(cmd)}")
    if not dry:
        subprocess.run(cmd, check=check)


def snip_contents(contents: str, num: int) -> str:
    contents = contents.replace("\n", "")
    if len(contents) > num:
        return (
            contents[:num]
            + f"  [... {len(contents) - num} characters omitted."
            + " Pass --show-contents to print them in their entirety]"
        )
    return contents


def detect_container_runtime() -> str:
    """Auto-detect the installed container runtime in the system."""
    if shutil.which("docker"):
        return "docker"
    elif shutil.which("podman"):
        return "podman"
    else:
        return None


##########################
# OCI parsing logic
#
# Compatible with:
# * https://github.com/opencontainers/image-spec/blob/main/image-layout.md


def oci_print_info(parsed: dict, full: bool) -> None:
    print(f"The OCI tarball contains an index and {len(parsed) - 1} manifest(s):")
    print()
    print(f"Image digest: {parsed[1]['digest']}")
    for i, info in enumerate(parsed):
        print()
        if i == 0:
            print(f"Index ({info['path']}):")
        else:
            print(f"Manifest {i} ({info['path']}):")
        print(f"  Digest: {info['digest']}")
        print(f"  Media type: {info['media_type']}")
        print(f"  Platform: {info['platform'] or '-'}")
        contents = info["contents"] if full else snip_contents(info["contents"], 600)
        print(f"  Contents: {contents}")
    print()


def oci_normalize_path(path):
    if path.startswith("sha256:"):
        hash_algo, checksum = path.split(":")
        path = f"blobs/{hash_algo}/{checksum}"
    return path


def oci_get_file_from_tarball(tar: tarfile.TarFile, path: str) -> dict:
    """Get file from an OCI tarball.

    If the filename cannot be found, search again by prefixing it with "./", since we
    have encountered path names in OCI tarballs prefixed with "./".
    """
    try:
        return tar.extractfile(path).read().decode()
    except KeyError:
        if not path.startswith("./") and not path.startswith("/"):
            path = "./" + path
            try:
                return tar.extractfile(path).read().decode()
            except KeyError:
                pass
        raise


def oci_parse_manifest(tar: tarfile.TarFile, path: str, platform: dict | None) -> dict:
    """Parse manifest information in JSON format.

    Interestingly, the platform info for a manifest is not included in the
    manifest itself, but in the descriptor that points to it. So, we have to
    carry it from the previous manifest and include in the info here.
    """
    path = oci_normalize_path(path)
    contents = oci_get_file_from_tarball(tar, path)
    digest = "sha256:" + hashlib.sha256(contents.encode()).hexdigest()
    contents_dict = json.loads(contents)
    media_type = get_key(contents_dict, "mediaType")
    manifests = contents_dict.get("manifests", [])

    if platform:
        os = get_key(platform, "os")
        arch = get_key(platform, "architecture")
        platform = f"{os}/{arch}"

    return {
        "path": path,
        "contents": contents,
        "digest": digest,
        "media_type": media_type,
        "platform": platform,
        "manifests": manifests,
    }


def oci_parse_manifests_dfs(
    tar: tarfile.TarFile, path: str, parsed: list, platform: dict | None = None
) -> None:
    info = oci_parse_manifest(tar, path, platform)
    parsed.append(info)
    for m in info["manifests"]:
        oci_parse_manifests_dfs(tar, m["digest"], parsed, m.get("platform"))


def oci_parse_tarball(path: Path) -> dict:
    parsed = []
    with tarfile.TarFile.open(path) as tar:
        oci_parse_manifests_dfs(tar, "index.json", parsed)
    return parsed


##########################
# Builder class


class Builder:
    """Builder for reproducible container images.

    Configures the build environment and executes container image builds
    with deterministic outputs.
    """

    def __init__(
        self,
        context: str,
        runtime: str | None = None,
        source_date_epoch: int | None = None,
        datetime: str | None = None,
        buildkit_image: str = DEFAULT_BUILDKIT_IMAGE,
        no_cache: bool = False,
        rootless: bool = False,
        file: str | None = None,
        output: str | Path | None = None,
        tag: str | None = None,
        build_arg: list[str] | None = None,
        annotation: list[str] | None = None,
        platform: str | None = None,
        buildkit_args: str | None = None,
        buildx_args: str | None = None,
        dry: bool = False,
    ):
        runtime = self._resolve_runtime(runtime)
        rootless = self._resolve_rootless(runtime, rootless)
        buildkit_image = self._resolve_buildkit_image(buildkit_image, rootless, runtime)

        self.context = self._resolve_path(context)
        self.runtime = runtime
        self.rootless = rootless
        self.buildkit_image = buildkit_image
        self.source_date_epoch = self._resolve_sde(source_date_epoch, datetime)
        self.use_cache = self._resolve_use_cache(no_cache)
        self.no_cache = no_cache
        self.file = self._resolve_path(file)
        self.output = Path(self._resolve_path(output) or str(Path.cwd() / "image.tar"))
        self.tag = tag
        self.build_arg = build_arg or []
        self.annotation = annotation or []
        self.platform = platform
        self.buildkit_args = self._resolve_buildkit_args(buildkit_args, runtime)
        self.buildx_args = self._resolve_buildx_args(buildx_args, runtime)
        self.dry = dry

    def _resolve_runtime(self, runtime: str | None) -> str:
        if runtime is not None:
            return runtime

        runtime = os.environ.get(ENV_RUNTIME)
        if runtime is None:
            raise RuntimeError("No container runtime detected in your system")
        if runtime not in ("docker", "podman"):
            raise RuntimeError(
                "Only 'docker' or 'podman' container runtimes"
                " are currently supported by this script"
            )
        return runtime

    def _resolve_use_cache(self, no_cache: bool) -> bool:
        if no_cache:
            return False
        return bool(int(os.environ.get(ENV_CACHE, "1")))

    def _resolve_rootless(self, runtime: str, rootless: bool) -> bool:
        rootless = rootless or bool(int(os.environ.get(ENV_ROOTLESS, "0")))
        if runtime != "podman" and rootless:
            raise RuntimeError("Rootless mode is only supported with Podman runtime")
        return rootless

    def _resolve_sde(self, source_date_epoch: int | None, datetime_str: str | None) -> int:
        sde = os.environ.get(ENV_SDE, source_date_epoch)
        dt = os.environ.get(ENV_DATETIME, datetime_str)

        if (sde is not None and dt is not None) or (sde is None and dt is None):
            raise RuntimeError(
                "You need to pass either a source date epoch or a datetime"
            )

        if sde is not None:
            return int(sde)

        d = datetime.datetime.fromisoformat(dt)
        if d.tzinfo is None or d.tzinfo.utcoffset(d) is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return int(d.timestamp())

    def _resolve_buildkit_image(self, buildkit_image: str, rootless: bool, runtime: str) -> str:
        if rootless and not buildkit_image.startswith("docker.io/"):
            buildkit_image = "docker.io/" + buildkit_image

        if runtime == "podman" and not buildkit_image.startswith("docker.io/"):
            buildkit_image = "docker.io/" + buildkit_image

        return buildkit_image

    def _resolve_buildkit_args(self, buildkit_args: str | None, runtime: str) -> list:
        if not buildkit_args:
            return []

        if runtime != "podman":
            raise RuntimeError(
                "Cannot specify BuildKit arguments using the Docker runtime"
            )

        return shlex.split(buildkit_args)

    def _resolve_buildx_args(self, buildx_args: str | None, runtime: str) -> list:
        if not buildx_args:
            return []

        if runtime != "docker":
            raise RuntimeError(
                "Cannot specify Docker Buildx arguments using the Podman runtime"
            )

        return shlex.split(buildx_args)

    def _resolve_path(self, path: str | Path | None) -> str | None:
        return path and str(Path(path).absolute())

    def _podman_build(self):
        rootless_args = []
        rootful_args = []
        if self.rootless:
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
        if self.file:
            dockerfile_args_podman = ["-v", f"{self.file}:/tmp/Dockerfile"]
            dockerfile_args_buildkit = ["--local", "dockerfile=/tmp"]
        else:
            dockerfile_args_buildkit = ["--local", "dockerfile=/tmp/work"]

        tag_args = f",name={self.tag}" if self.tag else ""

        annotation_args = ""
        for arg in self.annotation:
            annotation_args += f",annotation.{arg}"

        cache_args = []
        if self.use_cache:
            cache_args = [
                "--export-cache",
                "type=local,mode=max,dest=/tmp/cache",
                "--import-cache",
                "type=local,src=/tmp/cache",
            ]

        _build_args = []
        for arg in self.build_arg:
            _build_args.append("--opt")
            _build_args.append(f"build-arg:{arg}")
        platform_args = (
            ["--opt", f"platform={self.platform}"] if self.platform else []
        )

        cmd = [
            "podman",
            "run",
            "-it",
            "--rm",
            "-v",
            "buildkit_cache:/tmp/cache",
            "-v",
            f"{self.output.parent}:/tmp/image",
            "-v",
            f"{self.context}:/tmp/work",
            "--entrypoint",
            "buildctl-daemonless.sh",
            *rootless_args,
            *rootful_args,
            *dockerfile_args_podman,
            self.buildkit_image,
            "build",
            "--frontend",
            "dockerfile.v0",
            "--local",
            "context=/tmp/work",
            "--opt",
            f"build-arg:SOURCE_DATE_EPOCH={self.source_date_epoch}",
            *_build_args,
            "--output",
            f"type=docker,dest=/tmp/image/{self.output.name},rewrite-timestamp=true{tag_args}{annotation_args}",
            *cache_args,
            *dockerfile_args_buildkit,
            *platform_args,
            *self.buildkit_args,
        ]

        run(cmd, self.dry)

    def _docker_build(self):
        builder_id = hashlib.sha256(self.buildkit_image.encode()).hexdigest()
        builder_name = f"repro-build-{builder_id}"
        tag_args = ["-t", self.tag] if self.tag else []
        cache_args = [] if self.use_cache else ["--no-cache", "--pull"]

        cmd = [
            "docker",
            "buildx",
            "create",
            "--name",
            builder_name,
            "--driver-opt",
            f"image={self.buildkit_image}",
        ]
        run(cmd, self.dry, check=False)

        dockerfile_args = ["-f", self.file] if self.file else []
        _build_args = []
        for arg in self.build_arg:
            _build_args.append("--build-arg")
            _build_args.append(arg)

        _annotations = []
        for arg in self.annotation:
            _annotations.append("--annotation")
            _annotations.append(arg)

        platform_args = ["--platform", self.platform] if self.platform else []

        cmd = [
            "docker",
            "buildx",
            "--builder",
            builder_name,
            "build",
            "--build-arg",
            f"SOURCE_DATE_EPOCH={self.source_date_epoch}",
            *_build_args,
            *_annotations,
            "--provenance",
            "false",
            "--output",
            f"type=docker,dest={self.output},rewrite-timestamp=true",
            *cache_args,
            *tag_args,
            *dockerfile_args,
            *platform_args,
            *self.buildx_args,
            self.context,
        ]
        run(cmd, self.dry)

    def _log_context(self):
        logger.info(
            MSG_BUILD_CTX.format(
                runtime=self.runtime,
                buildkit_image=self.buildkit_image,
                sde=self.source_date_epoch,
                rootless=self.rootless,
                use_cache=self.use_cache,
                context=self.context,
                dockerfile=self.file or "(not provided)",
                tag=self.tag or "(not provided)",
                output=self.output,
                build_args=",".join(self.build_arg) or "(not provided)",
                annotations=",".join(self.annotation) or "(not provided)",
                platform=self.platform or "(default)",
                buildkit_args=" ".join(self.buildkit_args) or "(not provided)",
                buildx_args=" ".join(self.buildx_args) or "(not provided)",
            )
        )

    def build(self):
        """Execute the reproducible build."""
        self._log_context()

        if self.runtime == "docker":
            self._docker_build()
        else:
            self._podman_build()


##########################
# Command logic


def build(args):
    try:
        Builder(
            context=args.context,
            runtime=args.runtime,
            source_date_epoch=args.source_date_epoch,
            datetime=args.datetime,
            buildkit_image=args.buildkit_image,
            no_cache=args.no_cache,
            rootless=args.rootless,
            file=args.file,
            output=args.output,
            tag=args.tag,
            build_arg=args.build_arg,
            annotation=args.annotation,
            platform=args.platform,
            buildkit_args=args.buildkit_args,
            buildx_args=args.buildx_args,
            dry=args.dry,
        ).build()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)


def analyze(args) -> None:
    expected_image_digest = _parse_image_digest(args)
    tarball_path = Path(args.tarball)

    parsed = oci_parse_tarball(tarball_path)
    oci_print_info(parsed, args.show_contents)

    if expected_image_digest:
        cur_digest = parsed[1]["digest"].split(":")[1]
        if cur_digest != expected_image_digest:
            raise Exception(
                f"The image does not have the expected digest: {cur_digest} != {expected_image_digest}"
            )
        print(f"✅ Image digest matches {expected_image_digest}")


def analyze_tarball(
    tarball: str | Path,
    expected_image_digest: str | None = None,
    show_contents: bool = False,
) -> dict:
    """Analyze an OCI image tarball.

    Args:
        tarball: Path to the OCI image tarball.
        expected_image_digest: Optional expected digest to verify against.
        show_contents: If True, print full file contents.

    Returns:
        The parsed OCI tarball information.
    """
    tarball_path = Path(tarball)
    parsed = oci_parse_tarball(tarball_path)
    oci_print_info(parsed, show_contents)

    if expected_image_digest:
        if ":" in expected_image_digest:
            expected_image_digest = expected_image_digest.split(":")[1]
        cur_digest = parsed[1]["digest"].split(":")[1]
        if cur_digest != expected_image_digest:
            raise Exception(
                f"The image does not have the expected digest: {cur_digest} != {expected_image_digest}"
            )
        print(f"✅ Image digest matches {expected_image_digest}")

    return parsed


def _parse_image_digest(args) -> str | None:
    if not args.expected_image_digest:
        return None
    parsed = args.expected_image_digest.split(":", 1)
    if len(parsed) == 1:
        return parsed[0]
    else:
        return parsed[1]


def define_build_cmd_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runtime",
        choices=["docker", "podman"],
        default=detect_container_runtime(),
        help="The container runtime for building the image (default: %(default)s)",
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
            "The BuildKit container image which will be used for building the"
            " reproducible container image. Make sure to pass the '-rootless'"
            " variant if you are using rootless Podman"
            " (default: docker.io/moby/buildkit:v0.19.0)"
        ),
    )
    parser.add_argument(
        "--source-date-epoch",
        "--sde",
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
        help="Run BuildKit in rootless mode (Podman only)",
    )
    parser.add_argument(
        "-f",
        "--file",
        metavar="FILE",
        default=None,
        help="Pathname of a Dockerfile",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=Path.cwd() / "image.tar",
        help="Path to save OCI tarball (default: %(default)s)",
    )
    parser.add_argument(
        "-t",
        "--tag",
        metavar="TAG",
        default=None,
        help="Tag the built image with the name %(metavar)s",
    )
    parser.add_argument(
        "--build-arg",
        metavar="ARG=VALUE",
        action="append",
        default=None,
        help="Set build-time variables",
    )
    parser.add_argument(
        "--annotation",
        metavar="KEY=VALUE",
        action="append",
        default=None,
        help="Append annotation to the image",
    )
    parser.add_argument(
        "--platform",
        metavar="PLAT1,PLAT2",
        default=None,
        help="Set platform for the image",
    )
    parser.add_argument(
        "--buildkit-args",
        metavar="'ARG1 ARG2'",
        default=None,
        help="Extra arguments for BuildKit (Podman only)",
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


def parse_args() -> dict:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    build_parser = subparsers.add_parser("build", help="Perform a build operation")
    build_parser.set_defaults(func=build)
    define_build_cmd_args(build_parser)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze an OCI tarball")
    analyze_parser.set_defaults(func=analyze)
    analyze_parser.add_argument(
        "tarball",
        metavar="FILE",
        help="Path to OCI image in .tar format",
    )
    analyze_parser.add_argument(
        "--expected-image-digest",
        metavar="DIGEST",
        default=None,
        help="The expected digest for the provided image",
    )
    analyze_parser.add_argument(
        "--show-contents",
        default=False,
        action="store_true",
        help="Show full file contents",
    )

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()

    if not hasattr(args, "func"):
        args.func = build
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
