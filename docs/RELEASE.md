# Reproducible distribution and license inventory

These are operator-run release gates, not evidence that a candidate has passed them. They never publish, read publishing credentials, acquire models, or bundle FFmpeg. Private/real-environment acceptance remains a separate deferred gate; generated-media checks and actual platform checks are still required.

## Candidate and build prerequisites

Use a trusted local Git checkout and Python 3.11 or newer. Finish candidate edits (including both version declarations) before running; the checker snapshots tracked and nonignored untracked files, including uncommitted candidate changes. Do not modify the checkout during a run. Ignored files are not build inputs. Symlink inputs are rejected. Keep private material and credentials outside release source inputs.

The backend is pinned to **setuptools 84.0.0**, the installed toolchain inspected during M9 implementation. It supports the project's SPDX `license` string and explicit `license-files`; setuptools documents that PEP 639 support began in 77.0.0 ([upstream configuration documentation](https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html)). The former `setuptools>=68` lower bound did not guarantee support for this metadata. A standalone `build` or `wheel` package is not required: the checker invokes the declared backend's standard PEP 517 `build_wheel` and `build_sdist` hooks directly. CI may equivalently build wheels using `pip wheel --no-deps --no-build-isolation` with the declared backend installed.

Provision tools explicitly; this is not a cross-platform dependency lock:

```sh
python -m venv /tmp/storylens-release-tools
/tmp/storylens-release-tools/bin/python -m pip install setuptools==84.0.0 packaging==26.3
```

On Windows use a temporary directory outside the checkout and `Scripts/python.exe` instead of `bin/python`. The driving interpreter needs pip >=22.3; target venvs are created without pip/ensurepip and managed using that driver's `pip --python`. `git` must be available. Dependency wheels must be available from explicitly permitted indexes or a provisioned wheelhouse. No build/publish credentials are necessary. Pinning the build backend alone does **not** lock runtime resolution, interpreter, OS, native dependencies, or compression library; retain the actual environment/inventory for each release target.

## Run the release gate

For an actual 0.9.0 candidate, for example:

```sh
/tmp/storylens-release-tools/bin/python scripts/release_check.py \
  --mcp --epoch 1704067200 --output-dir /tmp/storylens-0.9.0-candidate
```

The output directory must not already exist. By default dependency installation can use the public index; package-manager configuration and `PIP_*`/`PYTHON*`/`STORYLENS_*` environment overrides are not inherited by installer checks. For a deliberately offline run, stage platform/interpreter-compatible wheels first, then use:

```sh
python scripts/release_check.py --mcp --offline \
  --wheelhouse /path/to/reviewed-wheelhouse \
  --output-dir /path/to/new-candidate-output
```

The wheelhouse must contain the active base dependency closure and, for `--mcp`, the MCP extra's complete closure. Missing dependencies fail installation; the checker does not fake availability, omit failing extras, or fetch model assets. Without `--mcp`, the report claims only the core check and makes no MCP compatibility assertion. Use `--source` to select another checkout and `--epoch` to select a fixed ZIP-compatible source timestamp. Use the same epoch when comparing candidates.

### What success proves

1. Two fresh source snapshots build in temporary directories outside the checkout with fixed `SOURCE_DATE_EPOCH`, `PYTHONHASHSEED=0`, and UTC timezone.
2. Both wheels are **byte-identical**. Archive members are safe regular files/directories under the expected package/metadata roots, and every file's SHA-256/size matches wheel `RECORD`. The three packaged schema resources parse as JSON and the original license is present.
3. Repeated sdists have identical member names, file contents and permission modes. Symlinks, hardlinks, special files, duplicates, traversal and unexpected top-level roots are rejected. **Gzip header timestamps and tar timestamps/owners/order are not the sdist reproducibility criterion.** `sdist_byte_identical` discloses whether compressed archives also match; it may be false without misrepresenting member-content reproducibility.
4. A wheel rebuilt from the validated sdist is byte-identical to the source-built wheel. This catches an sdist that omits inputs required for the actual wheel.
5. A new venv installs the built wheel outside the checkout without system-site packages. `pip check` succeeds, import provenance is inside that venv, package and metadata versions match, packaged schemas load through `importlib.resources`, and the installed console entry point answers `--version` and `--help`. The core environment has **no MCP package**, and importing its CLI does not import MCP.
6. With `--mcp`, a separate fresh venv installs the same wheel's MCP extra and performs an actual stdio client/server initialize, tool discovery and packaged results-schema read using the installed official SDK. It does not claim every MCP tool or every supported SDK version has been acceptance-tested.

Any mismatch, unsafe payload, backend mismatch, failed install, CLI/import failure, or MCP failure exits nonzero with `Release verification FAILED` diagnostics. No artifacts are copied into the requested output directory until all selected checks pass. Successful JSON appears on stdout; with `--output-dir`, the verified wheel, first sdist, and `release-check.json` are retained. No automatic upload/publishing is implemented.

`MANIFEST.in` includes the operational/release scripts, guides, examples and Docker recipe in source archives; the runtime wheel remains the Python package, schemas and declared license. Source-archive helper availability does not turn a source tarball into a trusted Git checkout for release rebuilding; the checker deliberately snapshots a reviewed checkout.

The versioned release-check JSON contains project/version, epoch/backend, explicit wheel/sdist/rebuilt-wheel results, the excluded sdist metadata comparison scope, artifact names/SHA-256 values, and selected installed-check modes. Build/installer diagnostics are not embedded into that JSON. Reproducibility here concerns this source candidate and one host toolchain; it does not assert cross-OS byte identity.

## Installed dependency license inventory

Run the inventory with the **same interpreter/environment as the installed candidate**, not a separate tooling venv that lacks it. Install `packaging==26.3` explicitly there if necessary. This helper is read-only and does not install dependencies, contact registries, resolve unavailable packages, inspect secrets, or read model/media files.

```sh
python scripts/license_audit.py > installed-core-licenses.json
python scripts/license_audit.py --extra mcp > installed-mcp-licenses.json
python scripts/license_audit.py --extra heif --extra semantic --extra mcp \
  > installed-optional-licenses.json
```

Only request extras actually provisioned in that environment. The last command is an inventory request, **not** evidence that those dependencies work or exist on every platform. `--root NAME` inventories another installed root distribution. No-extra selection inventories the base active closure even when unrelated optional packages happen to be installed. Transitive requirements with extras activate those extras as well; dependency markers are evaluated against the running interpreter/platform. The tool does not just list the root's direct names or everything in `pip freeze`.

### JSON contract (`schema_version: "1.0"`)

This is SBOM-style structured evidence, **not a standardized SPDX/CycloneDX document**:

- `format`: `storylens-installed-license-inventory`; `root`, `requested_extras`, `scope`, and `marker_environment` identify the selection.
- `components`: installed distribution `name`, `version`, PyPI-style `purl`, `selected_extras`, sanitized project URL origins, and `license` evidence.
- `license`: upstream `spdx_expression_from_metadata`, syntactically normalized SPDX expression when valid, legacy `metadata_license`, **uninterpreted** license classifiers, declared license-file references, discovered installed license-file references with SHA-256, and explicit `unknowns`.
- `dependencies`: active closure edges, specifiers, requested child extras and a boolean indicating direct-URL requirements. Private/direct installation URLs are never emitted.
- `issues`: missing active dependencies, unknown requested extras, invalid/unevaluable requirements, or installed version mismatches. `closure_complete` is false when these prevent a clean declared installed closure; it is not a legal-completeness claim.
- `privacy` and `limitations` state the output boundary. There are no absolute installation paths, interpreter paths, environment values containing credentials, `direct_url.json` contents, or package download URLs. Project URLs retain public origins only: userinfo, path, query and fragment are omitted to avoid embedded tokens/local paths. License file references are package-relative; relative references escaping the installation root are excluded. License strings/classifiers are upstream metadata evidence, not host configuration.

An incomplete/inconsistent closure exits **1 after emitting JSON**, so retain the report even on failure. Missing/invalid SPDX evidence and absent license files remain explicit unknowns rather than inferred permissions; default exit success permits those unknowns for human review. `--strict` additionally exits 1 if any component has license unknowns. A legacy permissive-looking classifier is never promoted into a legal conclusion. `License-File` references and heuristic discovered file names do not prove all relevant native notices were found.

## Distribution/licensing review boundary

See the [M9 primary-source license findings](research/media-ecosystem.md#m9-addendum-distribution-license-evidence). StoryLens original MIT, Pillow MIT-CMU, NumPy BSD-3-Clause, wrapper/framework licenses, FFmpeg build terms and model terms are distinct. The MIT StoryLens declaration does not relicense dependency wheels. In particular, a BSD pillow-heif wrapper is not proof that its bundled codecs are BSD-only; audit the actual wheel's bundled licenses.

Maintain three separate records:

1. **Declared optional ecosystem:** what dependency declarations and upstream research say can be selected.
2. **Installed environment:** this command's marker/extra-selected distribution metadata closure, exact versions, and located notices on one interpreter/platform.
3. **Actual redistributed bytes:** final wheel/sdist/installer/container contents, native libraries, notices, hashes, exact FFmpeg build flags/source arrangements if supplied separately, and model checkpoints/revisions/terms if separately provisioned. Inspect this final deliverable rather than treating package metadata as the whole audit.

The release scripts do not bundle/download FFmpeg or weights. Framework licenses do not establish model redistribution rights. External services, native codecs, drivers and patents are outside the metadata inventory; no legal audit completeness is claimed.

### Container recipe is not a binary redistribution clearance

The repository's Dockerfile is a local self-hosting recipe; it does not publish an image. Its pinned Python base-image digest and explicit top-level Pillow/NumPy versions do not lock every apt/transitive dependency or make the container byte-reproducible. The optional MCP build argument selects another dependency closure.

The recipe installs Debian's FFmpeg package. Do not label a resulting image “all MIT” or assume upstream's LGPL baseline describes that actual Debian binary: inspect the installed FFmpeg configuration, linked libraries, exact Debian package version and `/usr/share/doc/ffmpeg/copyright`, along with each package's copyright/source records. Debian documents its [copyright-file policy](https://www.debian.org/doc/debian-policy/ch-docs.html#copyright-information) and [source-package retrieval](https://www.debian.org/doc/manuals/debian-reference/ch02.en.html#_source_packages). Review applicable GPL/LGPL notices and corresponding-source obligations for the precise binaries before redistributing an image. A Dockerfile or a package-download link alone is not a claim that redistribution obligations have been satisfied. No published or independently audited image is established by this guide.

## Remaining release gates

Run the repository's generated-media smoke/regression checks against the installed candidate, plus the chosen extras on each claimed Linux/macOS/Windows target and actual architectures. Retain the actual CI run results rather than substituting this guide for evidence. Exercise native-wheel loading and subprocess discovery on each target; review CPU/GPU/HEIF differences and unavailable extras explicitly. Signing, platform installers, reviewed container contents, and native/model redistribution require their own evidence.

Private/real-environment media quality, performance and hardware acceptance remain deferred until implementation is complete, as requested; neither a generated fixture nor the local release checker satisfies them. A passing local report is not a release authorization, full platform certification, patent opinion, or permission to redistribute arbitrary downloaded binaries/models.
