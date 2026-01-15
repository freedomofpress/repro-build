# repro-build

- [How it works](#how-it-works)
- [Features](#features)
- [Reproducible images in this repository](#reproducible-images-in-this-repository)
- [Usage](#usage)
  - [Build a container image locally](#build-a-container-image-locally)
  - [Build a container image on GitHub Actions](#build-a-container-image-on-github-actions)
  - [Analyze a container image in .tar format](#analyze-a-container-image-in-tar-format)
- [Tarball format](#tarball-format)
- [Multi-platform images](#multi-platform-images)
- [Sources of non-determinism](#sources-of-non-determinism)
- [Other considerations](#other-considerations)
- [Read more](#read-more)
- [Credits](#credits)

`repro-build` is a script that helps you build bit-for-bit reproducible
containers. By "reproducible containers", we refer to container images which can
be rebuilt at any time, anywhere, from the same **Dockerfile** and **build
environment**, and be bit-for-bit equal to the original container image.

`repro-build` cannot assist you if your Dockerfile has sources of
non-determinism in it. What it does though is help you with the second part of
the equation, which is providing you with a build environment that is consistent
across Operating Systems and container engines.

> [!TIP]
> You can find some tools to make your container images reproducible in
> https://github.com/reproducible-containers.

To demonstrate why reproducibly building a container image requires more than a
"deterministic" Dockerfile, here's an example. Let's build the `scratch` image,
arguably the most deterministic image possible, with Docker and Podman:

```console
$ echo "FROM scratch" | docker build -
[+] Building 0.0s (3/3) FINISHED                                                                                                                                                                                               docker:default
 => [internal] load build definition from Dockerfile                                                                                                                                                                                     0.0s
 => => transferring dockerfile: 87B                                                                                                                                                                                                      0.0s
 => [internal] load .dockerignore                                                                                                                                                                                                        0.0s
 => => transferring context: 2B                                                                                                                                                                                                          0.0s
 => exporting to image                                                                                                                                                                                                                   0.0s
 => => writing image sha256:3302e88f529a4acbc0bb93fe2e2c2da7fa5a4d70e348d54f5736b604b7293c46
 ```

 ```console
$ echo "FROM scratch" | podman build -
STEP 1/1: FROM scratch
COMMIT
--> bcccfc6e10db
bcccfc6e10db600c78e86128f96c35d749e9c50aac2c7acd78874a4cbfaa51a0
$ podman images bcccfc6e10db --digests
REPOSITORY  TAG         DIGEST                                                                   IMAGE ID      CREATED        SIZE
<none>      <none>      sha256:251f716255f1732552091986ba7365fc195bae436a16d0d8e5a45e31adba97f0  bcccfc6e10db  2 minutes ago  1.06 kB
```

You can see that the image digests are different. That's not due to the contents
of the image (there are none after all), but due to the different types of
manifests and annotations that Podman and Docker use.

To make the digests exactly the same, you need to control various aspects of the
environment. `repro-build` saves you time by doing just that. Not only that, but
we have a nightly job which ensures that `repro-build` will continue to do so
for future versions of Docker, Podman, and BuildKit.

## How it works

In a nutshell, `repro-build` builds your container using a pinned version of
[BuildKit](https://github.com/moby/buildkit), and its reproducibility features.
If you use Docker, it creates a new buildx builder under the hood with a pinned
BuildKit version. If you are using Podman, it runs BuildKit within a container.
Then, it builds your container image, and stores it in tarball format. You can
analyze the image tarball later on and ensure it has the digest you expect.

## Features

- **GitHub Actions Support**: Built-in actions for building and verifying reproducible images in your CI/CD pipelines.
- **Pinned BuildKit**: Uses a pinned BuildKit version (v0.19.0 by default) to ensure environment consistency.
- **[`SOURCE_DATE_EPOCH`](https://github.com/moby/buildkit/blob/master/docs/build-repro.md#source_date_epoch) Control**: Accepts timestamps as Unix epochs or RFC 3339 datetimes to normalize file modification times.
- **Automatic Timestamp Normalization**: Passes `rewrite-timestamp=true` to BuildKit, ensuring image layers have predictable timestamps.
- **Provenance Disabling**: Automatically disables provenance creation, which often introduces non-determinism.
- **Multi-Runtime Support**: Works seamlessly with both Docker (via Buildx) and Podman.
- **Rootless Support**: Can run in rootless mode with Podman for enhanced security.
- **Analysis Tool**: Built-in `analyze` command to inspect OCI/Docker tarballs and verify digests.
- **Reproducible Base Images**: Provides daily-updated reproducible images (currently Debian-only).

## Reproducible images in this repository

This repository automatically builds and publishes several reproducible images
to GHCR. These images are updated daily and verified for reproducibility across
different environments and BuildKit versions.

| Distro | Dockerfile | GHCR Link |
|--------|------------|-----------|
| Debian | [Dockerfile.debian](Dockerfile.debian) | [ghcr.io/freedomofpress/repro-build/debian](https://ghcr.io/freedomofpress/repro-build/debian) |

## Usage

### Build a container image locally

You can build a container image with:

```console
$ ./repro-build build --sde 0 .
2025-02-24 09:17:48 - INFO - Build environment:
- Container runtime: docker
- BuildKit image: moby/buildkit:v0.19.0@sha256:14aa1b4dd92ea0a4cd03a54d0c6079046ea98cd0c0ae6176bdd7036ba370cbbe
- Rootless support: False
- Caching enabled: True
- Build context: ./repro-build
- Dockerfile: (not provided)
- Output: ./repro-build/image.tar

Build parameters:
- SOURCE_DATE_EPOCH: 0
- Build args: (not provided)
- Tag: (not provided)
- Platform: (default)

Podman-only arguments:
- BuildKit arguments: (not provided)

Docker-only arguments:
- Docker Buildx arguments: (not provided)

2025-02-24 09:17:48 - DEBUG - Running: docker buildx create --name repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056 --driver-opt image=moby/buildkit:v0.19.0@sha256:14aa1b4dd92ea0a4cd03a54d0c6079046ea98cd0c0ae6176bdd7036ba370cbbe
ERROR: existing instance for "repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056" but no append mode, specify the node name to make changes for existing instances
2025-02-24 09:17:48 - DEBUG - Running: docker buildx --builder repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056 build --build-arg SOURCE_DATE_EPOCH=0 --provenance false --output type=docker,dest=/Users/alex.p/repro-build/image.tar,rewrite-timestamp=true /Users/alex.p/repro-build
[+] Building 81.6s (7/7) FINISHED                                                                                                               docker-container:repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056
 => [internal] load build definition from Dockerfile                                                                                                                                                                                     0.0s
 => => transferring dockerfile: 522B                                                                                                                                                                                                     0.0s
 => [internal] load metadata for docker.io/library/debian:bookworm-20230904-slim                                                                                                                                                         0.4s
 => [internal] load .dockerignore                                                                                                                                                                                                        0.0s
 => => transferring context: 2B                                                                                                                                                                                                          0.0s
 => [internal] load build context                                                                                                                                                                                                        0.0s
 => => transferring context: 5.49kB                                                                                                                                                                                                      0.0s
 => CACHED [stage-0 1/2] FROM docker.io/library/debian:bookworm-20230904-slim@sha256:050f00e86cc4d928b21de66096126fac52c2ea47885c232932b2e4c00f0c116d                                                                                    0.0s
 => => resolve docker.io/library/debian:bookworm-20230904-slim@sha256:050f00e86cc4d928b21de66096126fac52c2ea47885c232932b2e4c00f0c116d                                                                                                   0.0s
 => [stage-0 2/2] RUN   --mount=type=cache,target=/var/cache/apt,sharing=locked   --mount=type=cache,target=/var/lib/apt,sharing=locked   --mount=type=bind,source=./repro-sources-list.sh,target=/usr/local/bin/repro-sources-list.sh  70.1s
 => exporting to docker image format                                                                                                                                                                                                    11.0s
 => => exporting layers                                                                                                                                                                                                                  5.1s
 => => rewriting layers with source-date-epoch 0 (1970-01-01 00:00:00 +0000 UTC)                                                                                                                                                         5.2s
 => => exporting manifest sha256:d2ed9626c60a7ea2b774b1e268ba74f1839de34808ed32ff99f9f7facde4de0b                                                                                                                                        0.0s
 => => exporting config sha256:b1fbf0683ddec2760c7cc4fada2cff4a28a6654958902ba42e6fc58295ead88e                                                                                                                                          0.0s
 => => sending tarball
```

#### `build` options

| Option | Description |
|--------|-------------|
| `--runtime` | Container runtime (`docker` or `podman`). Auto-detected if not provided. |
| `--datetime` | ISO format datetime for image layers. |
| `--source-date-epoch`, `--sde` | Unix timestamp for image layers. |
| `--buildkit-image` | Custom BuildKit image to use. |
| `--no-cache` | Build from scratch without using cache. |
| `--rootless` | Run BuildKit in rootless mode (Podman only). |
| `-f`, `--file` | Path to the Dockerfile. |
| `-o`, `--output` | Path to save the output tarball (default: `image.tar`). |
| `-t`, `--tag` | Tag the built image. |
| `--build-arg` | Set build-time variables (can be used multiple times). |
| `--annotation` | Add image annotations (can be used multiple times). |
| `--platform` | Target platform(s) for the build. |
| `--buildkit-args` | Extra arguments for BuildKit (Podman only). |
| `--buildx-args` | Extra arguments for Docker Buildx (Docker only). |
| `--dry` | Dry-run mode (prints commands instead of running them). |

### Build a container image on GitHub Actions

This repository provides two GitHub Actions to help you build and verify
reproducible images.

#### Reproducible build action (`freedomofpress/repro-build@v1`)

This action builds a container image reproducibly using Docker Buildx and the
standard `docker/build-push-action`. It is a wrapper that handles
`SOURCE_DATE_EPOCH` validation and ensures the `rewrite-timestamp=true` output
option is set.

**Example Usage:**

```yaml
- name: Reproducibly build and push image
  uses: freedomofpress/repro-build@v1
  with:
    tags: ghcr.io/my-org/my-image:latest
    file: Dockerfile
    platforms: linux/amd64,linux/arm64
    source_date_epoch: 1677619260
    push: true
```

##### Inputs

| Name | Description | Default |
|------|-------------|---------|
| `tags` | Tags for the image (comma-separated). | |
| `file` | Path to the Dockerfile. | |
| `context` | Build context. | |
| `platforms` | Platforms to build for (e.g., `linux/amd64,linux/arm64`). | |
| `buildkit_image` | BuildKit image to use. | `moby/buildkit:v0.19.0@...` |
| `source_date_epoch` | `SOURCE_DATE_EPOCH` value. | |
| `timestamp` | RFC 3339 timestamp to use as `SOURCE_DATE_EPOCH`. | |
| `push` | Whether to push to registry. | `false` |
| `outputs` | List of output destinations. | |
| `annotations` | List of annotations to set. | |
| `labels` | List of metadata for an image. | |
| `build-args` | List of build-time variables. | |
| `cache` | Whether to enable caching. | `true` |
| `cache-from` | List of external cache sources. | |
| `cache-to` | List of external cache destinations. | |
| `cache-map` | Mapping for `buildkit-cache-dance`. | |
| `secrets` | List of secrets to expose to the build. | |
| `secret-files` | List of secret files to expose to the build. | |
| `ssh` | List of SSH agent socket or keys. | |
| `target` | Sets the target stage to build. | |
| `no-cache` | Do not use cache. | `false` |
| `pull` | Always attempt to pull a newer version of the image. | `false` |
| `sbom` | Generate SBOM attestation. | `false` |
| `no-setup-buildx` | Whether to skip the Buildx setup step. | `false` |

##### Outputs

| Name | Description |
|------|-------------|
| `imageid` | Image ID. |
| `digest` | Image digest. |
| `metadata` | Build metadata. |

#### Reproduce and verify action (`freedomofpress/repro-build/verify@v1`)

Rebuilds an image and verifies its digest against an expected value or a target image.

**Example Usage:**

```yaml
- name: Verify image reproducibility
  uses: freedomofpress/repro-build/verify@v1
  with:
    target_image: ghcr.io/my-org/my-image:latest
    file: Dockerfile
    platforms: linux/amd64
    source_date_epoch: 1677619260
    runtime: podman
```

##### Inputs

| Name | Description | Default |
|------|-------------|---------|
| `expected_digest` | Expected image digest (e.g., `sha256:...`). | |
| `target_image` | Image to fetch digest from for verification. | |
| `file` | Path to the Dockerfile. | `Dockerfile` |
| `context` | Build context. | `.` |
| `platforms` | Target platform (e.g., `linux/amd64`). | `linux/amd64` |
| `buildkit_image` | BuildKit image to use. | |
| `runtime` | Container runtime (`docker` or `podman`). | `podman` |
| `source_date_epoch` | `SOURCE_DATE_EPOCH` value. | |
| `build-args` | Additional build arguments (comma-separated `ARG=VALUE`). | |
| `output` | Path to save the image tarball. | `/tmp/image.tar` |
| `tags` | Tags for the image. | |

### Analyze a container image in .tar format

You can inspect the created tarball with:

```console
$ ./repro-build analyze image.tar
The OCI tarball contains an index and 1 manifest(s):

Image digest: sha256:d2ed9626c60a7ea2b774b1e268ba74f1839de34808ed32ff99f9f7facde4de0b

Index (index.json):
  Digest: sha256:e609199e7b564eba29ee3ccaa8509fed8c62a8ac91ee5caba46c9c0dc0ed6129
  Media type: application/vnd.oci.image.index.v1+json
  Platform: -
  Contents: {"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[{"mediaType":"application/vnd.docker.distribution.manifest.v2+json","digest":"sha256:d2ed9626c60a7ea2b774b1e268ba74f1839de34808ed32ff99f9f7facde4de0b","size":703,"annotations":{"org.opencontainers.image.created":"1970-01-01T00:00:00Z"},"platform":{"architecture":"arm64","os":"linux"}}]}

Manifest 1 (blobs/sha256/d2ed9626c60a7ea2b774b1e268ba74f1839de34808ed32ff99f9f7facde4de0b):
  Digest: sha256:d2ed9626c60a7ea2b774b1e268ba74f1839de34808ed32ff99f9f7facde4de0b
  Media type: application/vnd.docker.distribution.manifest.v2+json
  Platform: linux/arm64
  Contents: {  "schemaVersion": 2,  "mediaType": "application/vnd.docker.distribution.manifest.v2+json",  "config": {    "mediaType": "application/vnd.docker.container.image.v1+json",    "digest": "sha256:b1fbf0683ddec2760c7cc4fada2cff4a28a6654958902ba42e6fc58295ead88e",    "size": 1165  },  "layers": [    {      "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",      "digest": "sha256:155eab17d86c47443adc8cebe7fc62c847c03db8cfb1ca53aa6276564fff23ef",      "size": 29157149    },    {      "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",      "digest": "sha256:7914d3c3eb039f  [... 83 characters omitted. Pass --show-contents to print them in their entirety]
```

## Tarball format

By default, `repro-build` uses the
[`docker` exporter](https://docs.docker.com/build/exporters/) when creating an
image tarball.

Pros and cons of `docker` exporter:
* :+1: The image manifest produced by the `docker` exporter matches the one that
  BuildKit produces when pushing an image to a Docker Registry. In layman terms,
  this means that the `docker` exporter allows you to compare local digests with
  remote ones.
* :-1: You cannot build multi-platform tarballs

Pros and cons of `oci` exporter:
* :+1: You can build multi-platform tarballs, which you can load with Podman
* :-1: Tarballs in `oci` format cannot be consumed by `docker load`

We feel it's more important to compare local digests with remote ones, as well
as load the container image with `docker load`, so we prefer to use the `docker
load` exporter.

## Multi-platform images

If you are on macOS / Windows, the easiest way to build multi-platform images is
via Docker, which has built-in BuildKit support. Any other option may require
nested virtualization to work.

The `docker` exporter that `repro-build` uses under the hood does not support
multi-platform images. You are advised to create a tarball per architecture, if
you want to reproduce an image.

If you want to build and push an image, it's best to swap `type=docker` with
`type=registry` manually. You can try out a build with `./repro-build build
--dry ...`, and tweak the commands that would have ran.

If you want to build and push images with Podman, you may also need to mount the
registry credentials in the BuildKit container.

## Sources of non-determinism

Here are some lesser known sources of non-determinism that we have encountered
while building images:

* `COPY` commands in containerized Buildkit may work differently than `COPY`
  commands in Docker. We have seen permissions changing from `drwxr-xr-x` to
  `drwxr-sr-x`.
* Using datetimes in your commands without specifying a timezone may work for
  the region you're at, but not in a different country.
* Adding a user to the container image means that an entry is added in
  `/etc/shadow`. This entry contains the day the user was first added, which
  means that such images are not reproducible the next day. We suggest appending
  `&& chage -d 99999 <user> && rm /etc/shadow-` in your `adduser` command.
* If you attempt to copy `/etc` during image creation to a different place, you
  may also copy the mounted `/etc/resolv.conf` file, which contains info about
  your DNS resolvers.

## Other considerations

* The arguments you pass to the script must be tracked somehow, if you want to
  rebuild your container image in the future. Best way to track them is in your
  Git repo. Else, you may want to add them in your tag, or as labels.

## Read more

For a primer on what are "reproducible containers", and some sources
to get started, we suggest reading the following:
* https://medium.com/nttlabs/dockercon-2023-reproducible-builds-with-buildkit-for-software-supply-chain-security-0e5aedd1aaa7
* https://github.com/reproducible-containers/
* https://github.com/moby/buildkit/blob/master/docs/build-repro.md
* https://wiki.debian.org/ReproducibleBuilds/About

## Credits

Credits go to [@AkihiroSuda](https://github.com/AkihiroSuda) who has provided
the necessary scaffolding (see
[https://github.com/reproducible-containers](https://github.com/reproducible-containers))
that this project is based on.
