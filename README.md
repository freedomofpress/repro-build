# repro-build

`repro-build.py` is a script that lets you build bit-for-bit reproducible
containers. For a primer on what are "reproducible containers", and some sources
to get started, we suggest reading the following:
* https://medium.com/nttlabs/dockercon-2023-reproducible-builds-with-buildkit-for-software-supply-chain-security-0e5aedd1aaa7
* https://github.com/reproducible-containers/

The `repro-build.py` script focuses on erasing a source of non-determinism when
building containers; the toolchain available on a system, and its version. It
uses Buildkit under the hood, and pins it to a specific version. It also passes
a few more options like `SOURCE_DATE_EPOCH` and `rewrite-timestamp=true`, to
ensure bit-for-bit reproducibility.

```
./repro-build.py --datetime 2025-02-07T11:34 .
```

In GitHub actions, you can use the following invocation:

```
...
```

## Environment variables

## Tips and tricks

* The arguments you pass to the script must be tracked somehow, if you want to
  rebuild your container image in the future. Best way to track them is in your
  Git repo. Else, you may want to add them in your tag, or as labels.
* For multi-platform builds on macOS and Windows, we suggest using the Docker
  container runtime, which has native support for multiple architectures.
* Specify a timezone for the build timestamp, so that it's the same across
  regions.
