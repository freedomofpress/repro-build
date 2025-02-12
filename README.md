# repro-build

`repro-build` is a script that helps you build bit-for-bit reproducible
containers. By "reproducible containers", we refer to container images which can
be rebuilt at any time, from the same **Dockerfile** and **build environment**,
and be bit-for-bit equal to the original container image.

`repro-build` cannot assist you if your Dockerfile has sources of
non-determinism in it. What it does though is help you with the second part of
the equation, which is providing you with a build environment that is consistent
across Operating Systems and container engines.

Btw, if you think that a deterministic Dockerfile is all you need, think again.
Here's Docker and Podman returning different digests for the `scratch` image,
arguably the most deterministic image possible.

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

## How it works

In a nutshell, `repro-build` builds your container using a pinned version of
[Buildkit](https://github.com/moby/buildkit), and its reproducibility features.
If you use Docker, it creates a new buildx builder under the hood with a pinned
Buildkit version. If you are using Podman, it runs Buildkit within a container.
Then, it builds your container image, and stores it in tarball format. You can
analyze the image tarball later on and ensure it has the digest you expect.

## Features

- Uses Buildkit >= 0.13 under the hood, but you can also provide your own image
- Requires
  [`SOURCE_DATE_EPOCH`](https://github.com/moby/buildkit/blob/master/docs/build-repro.md#source_date_epoch)
  or reads it from an ISO datetime
- Passes `rewrite-timestamp=true` to make image layers have files with the same
  timestamp
- Disables provenance creation, which conflicts with reproducibility
- Tested against various versions of Podman and Docker
- Can run in rootless mode (Podman-only)
- Supports a dry-run mode which prints the commands that would run

## Usage

You can build a container image with:

```console
$ ./repro-build build --source-date-epoch 0 .
2025-02-17 13:54:20 - INFO - Build parameters:
- Container runtime: docker
- Buildkit image: moby/buildkit:v0.19.0@sha256:14aa1b4dd92ea0a4cd03a54d0c6079046ea98cd0c0ae6176bdd7036ba370cbbe
- SOURCE_DATE_EPOCH: 0
- Rootless support: False
- Caching enabled: True
- Build context: /Users/apyrgio/repro-build
- Dockerfile: (not provided)
- Tag: (not provided)
- Output: /Users/apyrgio/repro-build/image.tar
- Buildkit arguments: (not provided)
- Docker Buildx arguments: (not provided)

2025-02-17 13:54:20 - DEBUG - Running: docker buildx create --name repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056 --driver-opt image=moby/buildkit:v0.19.0@sha256:14aa1b4dd92ea0a4cd03a54d0c6079046ea98cd0c0ae6176bdd7036ba370cbbe
ERROR: existing instance for "repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056" but no append mode, specify the node name to make changes for existing instances
2025-02-17 13:54:20 - DEBUG - Running: docker buildx --builder repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056 build --build-arg SOURCE_DATE_EPOCH=0 --provenance false --output type=oci,dest=/Users/apyrgio/repro-build/image.tar,rewrite-timestamp=true -f /Users/apyrgio/repro-build/Dockerfile /Users/apyrgio/repro-build
[+] Building 0.0s (3/3) FINISHED                                                                                                                docker-container:repro-build-6eb8a59ad67f3a251f19d5abdd82689923fe4f501a97a8fee73eeb935538a056
 => [internal] load build definition from Dockerfile                                                                                                                                                                                     0.0s
 => => transferring dockerfile: 50B                                                                                                                                                                                                      0.0s
 => [internal] load .dockerignore                                                                                                                                                                                                        0.0s
 => => transferring context: 2B                                                                                                                                                                                                          0.0s
 => exporting to oci image format                                                                                                                                                                                                        0.0s
 => => exporting layers                                                                                                                                                                                                                  0.0s
 => => rewriting layers with source-date-epoch 0 (1970-01-01 00:00:00 +0000 UTC)                                                                                                                                                         0.0s
 => => exporting manifest sha256:6433bd305d3eae24c1561122636995c4fd0c409b42ec702e731c52553129fd86                                                                                                                                        0.0s
 => => exporting config sha256:3302e88f529a4acbc0bb93fe2e2c2da7fa5a4d70e348d54f5736b604b7293c46                                                                                                                                          0.0s
 => => sending tarball
```

You can then inspect the created tarball with:

```console
$ ./repro-build analyze image.tar

The OCI tarball contains 1 manifest(s):

Manifest 1:
  Digest: sha256:6433bd305d3eae24c1561122636995c4fd0c409b42ec702e731c52553129fd86
  Media type: application/vnd.oci.image.manifest.v1+json
  Platform: linux/arm64
  Contents: {'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'digest': 'sha256:6433bd305d3eae24c1561122636995c4fd0c409b42ec702e731c52553129fd86', 'size': 288, 'annotations': {'org.opencontainers.image.created': '1970-01-01T00:00:00Z'}, 'platform': {'architecture': 'arm64', 'os': 'linux'}}
```

For more options, pass the `--help` flag.

## Things to consider

### General

* The arguments you pass to the script must be tracked somehow, if you want to
  rebuild your container image in the future. Best way to track them is in your
  Git repo. Else, you may want to add them in your tag, or as labels.
* Specify a timezone for the build timestamp, so that it's the same across
  regions.

### Multi-platform images

* If you are on macOS / Windows, the easiest way to build multi-platform images
  is via Docker, which has built-in Buildkit support. Any other options may
  require nested virtualization to work.
* At the same time, Docker has very limited support for working locally with
  multi-platform container images. It cannot load them from OCI image tarballs
  (`type=oci`), and cannot produce them as Docker image tarballs
  (`type=docker`). It can only push them to a container registry, while it
  creates them.
* See our
  [GitHub action](https://github.com/freedomofpress/repro-build/blob/main/.github/workflows/ci.yml)
  for an example of building a multi-platform image.

### Pushing images

* If you want to push images to a container registry, see what `./repro-build
  build --dry` returns, and tweak the `--output type=oci` part accordingly.
* For Podman, you may need to mount the Docker registry secrets in the Buildkit
  container.

### GitHub actions

* If you want to build a container image on GitHub actions, but you
  still need to do so reproducibly, try out the following:

  ```yaml
  - name: Build image
    uses: docker/build-push-action@v6
    with:
      provenance: false
      build-args: SOURCE_DATE_EPOCH=1677619260
      outputs: type=oci,dest=image.tar,name=image:latest,rewrite-timestamp=true
      platforms: linux/amd64,linux/arm64
      file: Dockerfile.debian-12
  ```

* This particular snippet is from a
  [GitHub action](https://github.com/freedomofpress/repro-build/blob/main/.github/workflows/ci.yml)
  that verifies nightly if the digests of the container images built with
  `repro-build` and GitHub actions are the same.


## Read more

For a primer on what are "reproducible containers", and some sources
to get started, we suggest reading the following:
* https://medium.com/nttlabs/dockercon-2023-reproducible-builds-with-buildkit-for-software-supply-chain-security-0e5aedd1aaa7
* https://github.com/reproducible-containers/
* https://github.com/moby/buildkit/blob/master/docs/build-repro.md
* https://wiki.debian.org/ReproducibleBuilds/About
