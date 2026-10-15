# Reusable media and AI ecosystem

Research date: 2026-10-15. Scope: a private, local-first, cross-platform storylens application; reusable components, not implementation. FilmCraft is not required by any recommendation here.

## Evidence and recommendation boundary

**Verified** means supported by the linked first-party documentation, repository, license text, or model metadata inspected during this research. It does **not** mean installed, benchmarked, packaged, or tested in this repository. **[INFERENCE]** marks engineering recommendations or conclusions drawn from that evidence. Online `main`, `master`, `latest`, and `stable` pages are mutable; their claims are not a dependency lockfile. No throughput, memory floor, model accuracy, complete camera/codec coverage, or OS-specific installer success is established for this application.

**Unavailable:** the raw Community-1 model-card download returned HTTP 401. Its public model metadata was accessible and reports gated access and CC BY 4.0; the pyannote repository independently documents acceptance/token requirements and local execution. Complete download conditions and all transitive model artifacts were not retrieved. Do not interpret an accessible public metadata record as authorization to redistribute gated weights. No commercial codec/patent clearance or binary-by-binary license audit was performed.

## Practical default

**[INFERENCE]** Use Python for orchestration and the first analysis workers, external FFmpeg/ffprobe for media probing/decoding, ExifTool for camera/file metadata, and Pillow for ordinary image derivatives. Add HEIF and camera RAW as explicitly detected capabilities. Keep OpenCV/PySceneDetect, speech recognition, speaker diarization, embeddings, and generative inference optional; they should not be imports or downloads required to scan a directory. Start with a CPU-capable path; offer accelerated profiles rather than promising one universal GPU backend.

**[INFERENCE]** Preserve originals, store evidence separately from model interpretations, and make every derived result traceable to source stream/item, actual presentation time, preprocessing, tool/model revision, and settings. Do not make an LLM or cloud service responsible for file identity, authoritative duration, precise frame timing, or metadata extraction. Install-time model fetching and runtime uploads are different permissions; require explicit choice for each.

## Capability map: what a component really supplies

| Need | Verified reusable capability | Boundary and recommendation |
| --- | --- | --- |
| Container/stream inventory | ffprobe emits machine-readable stream, format, packet and frame information; FFmpeg decodes, filters, extracts and transcodes [1, 2]. | **[INFERENCE]** Treat a successful probe and a successful decode as distinct states. Container extension alone does not establish a usable decoder. |
| Camera/file metadata | ExifTool reads EXIF, XMP, IPTC, GPS, maker notes and many image/audio/video formats [5]. | Metadata support is not pixel decoding. **[INFERENCE]** Keep original tag groups/values and unknown timezone state; metadata is evidence supplied by a file, not independently verified truth. |
| Still-image derivatives | Pillow supports numerous formats, image operations, and ICC transforms through LittleCMS2 [7, 8]. | HEIF and photographic RAW need additional components; broad Pillow support does not establish HDR-preserving processing. |
| HEIC/HEIF | pillow-heif wraps libheif, supports 8/10/12-bit HEIC and multiple images/primary image, metadata, depth and auxiliary images [9]. | Its Pillow plugin opens RGB/RGBA **8-bit**; the standalone interface exposes higher-bit-depth data [9, 10]. **[INFERENCE]** A thumbnail is not an HDR master or proof of handling every gain-map/auxiliary-image workflow. |
| Camera RAW | rawpy wraps LibRaw, can demosaic/postprocess, extract embedded previews and produce 16-bit linear output [12]. | Camera/model support depends on bundled LibRaw; some files lack a supported thumbnail. **[INFERENCE]** Do not claim support for all `.raw`, video RAW, or new camera models. |
| Cuts/fades | PySceneDetect has HSV-content/adaptive, intensity-threshold, histogram and perceptual-hash detectors [14]. | These detect visual transitions, not narrative scenes, subjects, actions or editorial importance. **[INFERENCE]** Retain detector/settings and expose boundaries as candidates. |
| Speech transcript | faster-whisper supplies Whisper inference, language information, segments, optional word timestamps and Silero VAD [17]. | It is not speaker identity, sound-event classification or a guarantee of exact word timing. Whisper explicitly documents hallucinations, repetition and uneven language/accent accuracy [19]. |
| Who spoke when | pyannote.audio supplies speaker diarization, including local Community-1; its premium pipeline can instead execute remotely [20]. | **[INFERENCE]** Speaker labels are clusters within a run, not people’s names or stable cross-library identities. Preserve overlap rather than silently forcing all audio into one speaker. |
| Semantic image search | CLIP and SigLIP image/text encoders support similarity/classification; SigLIP 2’s card explicitly identifies image-text retrieval [22, 23]. | They do not generate grounded captions, full transcripts, object tracks or reliable counts. **[INFERENCE]** Treat rankings as retrieval suggestions, not calibrated probabilities or factual annotations. |
| Descriptions and summaries | Local LLM/VLM runtimes can execute supported models; llama.cpp explicitly supports both LLM and VLM inference [24–26]. | The runtime is not the model. **[INFERENCE]** Text-only models cannot inspect images; a vision model seeing sampled frames does not establish full-motion understanding or complete event coverage. |

## 1. FFmpeg/ffprobe: authoritative media mechanics, build-dependent support

Verified: ffprobe can output JSON and expose stream/format/frame/packet sections [1]. FFmpeg documents decoding/encoding, stream selection, seeking, timestamp synchronization, hardware-device options and frame-rate conversion [2]. Availability of a named codec/filter/hardware path depends on how a binary was built and the host runtime; a product cannot infer capability from the name “FFmpeg” alone [2, 4].

**[INFERENCE]** Probe first and decode only the necessary streams. Retain stream index, codec, dimensions, sample aspect ratio, rotation/display transform, audio channels/sample rate, time base, reported start/duration, pixel format, color primaries/transfer/matrix/range and available HDR side data. Do not conflate a container’s duration with each stream’s duration. Use structured subprocess arguments rather than shell interpolation; accept local paths rather than arbitrary remote URLs by default.

### Timestamps, VFR and audio synchronization

Verified: FFmpeg distinguishes timestamp passthrough, CFR frame duplication/drop and VFR synchronization. `-copyts`, `-start_at_zero`, output synchronization and muxer behavior can change the timestamp origin or output timestamps. Input seeking commonly seeks to a nearby seek point; with accurate transcoding seek, intervening material is decoded/discarded, whereas stream-copy behavior differs [2]. PySceneDetect’s current timestamp API independently emphasizes `pts * time_base` rather than frame number divided by average FPS for VFR [15].

**[INFERENCE]** Use integer PTS plus rational time base as the retained media coordinate, with an explicitly stored source-to-analysis offset if a worker normalizes to zero. Derive decimal seconds for display, not as the only retained provenance. For VFR, never compute event positions as `frame_index / avg_frame_rate`. Keep requested sampling time and actual decoded presentation time distinct; keyframe seek accuracy is not a universal guarantee. Map speech/diarization segments back through audio extraction/chunk offsets. Store edit/SMPTE timecode separately from media elapsed time and EXIF capture date. If timestamps are missing, synthesized timing must be labeled as such.

### HDR and colors

Verified: FFmpeg’s `tonemap` filter expects linear-light data; its documentation shows transfer conversion around tone mapping. `zscale` and `libplacebo` offer color/scaling operations with their own build requirements [3]. pillow-heif’s normal Pillow plugin is 8-bit, despite the underlying library’s higher-bit-depth support [9, 10].

**[INFERENCE]** Make “analysis/display derivative” an explicit artifact type: record source transfer/primaries/range, orientation, target color space, tone-map method and output bit depth. Do not turn PQ/HLG pixels into an 8-bit RGB thumbnail merely by discarding precision or changing tags. Missing/conflicting color metadata should remain unknown or carry a documented assumption. Raw sensor brightness, display-referred tone-mapped brightness and an embedding input are different measurements. Color-accurate mastering, Dolby Vision metadata reconstruction and universal Ultra HDR/gain-map rendering are not established by this research.

### Licensing and packaging

Verified: FFmpeg is generally LGPL 2.1-or-later; enabling GPL components makes the FFmpeg build GPL. Its legal page recommends avoiding `--enable-gpl` and `--enable-nonfree` for its LGPL compliance path and includes corresponding-source, notices and relinking considerations. Codec patent exposure is a separate jurisdiction/use-dependent issue [4].

**[INFERENCE]** Prefer an explicitly configured/discovered external binary initially. For a bundled release, audit the exact configure flags and linked components, ship applicable notices and corresponding-source arrangements, and reject unreviewed “nonfree” builds. Running an executable as a subprocess is an architectural boundary, not an exemption from distributing that executable compliantly. Capture binary version/build configuration in capability reports. Do not silently obtain a codec-rich binary from an arbitrary download site.

## 2. ExifTool, Pillow, HEIF and RAW

### ExifTool

Verified: ExifTool’s own README identifies a Perl application/module library with broad metadata and maker-note support and dual terms “Perl Artistic License or GPL”; the repository’s GitHub summary alone can misleadingly show GPL only [5]. The application supports JSON and persistent `-stay_open` processing [6]. ExifTool documents that QuickTime integer date fields should be UTC by specification but many cameras write local time; it therefore does not assume UTC by default, while `QuickTimeUTC` changes interpretation [6a].

**[INFERENCE]** Use the CLI in read-only mode, requesting grouped metadata so identical tag names from different namespaces do not collide. Preserve EXIF date, offset tags, GPS time, QuickTime dates and filesystem times separately; do not repair ambiguity by treating the current machine’s timezone as the capture timezone. Never enable metadata writes as a side effect of analysis. A persistent ExifTool process is an optimization after simple batch calls are adequate, not a requirement of foundation. Bundle Perl/runtime and auxiliary files as appropriate for the chosen platform distribution, not just a renamed script [5].

### Pillow and HEIF

Verified: Pillow is MIT-CMU licensed [7]. It provides format-specific handling and ImageCms/LittleCMS2 profile transforms [8]. Current Pillow documentation includes native AVIF support but specifies decoded AVIF as 8-bit RGB(A); HEIC support is provided by an extra such as pillow-heif [8a, 9]. pillow-heif’s current wrapper license is BSD-3-Clause, while libheif has separate LGPL terms; its current `COPYING` includes LGPLv3 text [11]. libheif’s README warns that x265 is GPL and identifies BSD-licensed kvazaar as an alternative encoder; libheif can be built with selected codecs/plugins [11].

**[INFERENCE]** Use Pillow for JPEG/PNG and reviewed formats, with an explicit ICC-to-target-profile step and orientation handling. Use optional HEIF decoding only after detecting the extension’s native dependencies. Prefer a reviewed decode-only build for a read-only product rather than acquiring an unnecessary HEVC encoder. Do not assume the wrapper’s BSD license covers every wheel dependency; inspect the selected wheel’s bundled libheif/codecs and notices. Historical package versions and old PyPI classifiers are not reliable evidence of today’s binary license composition. Do not assume retaining 10/12-bit samples automatically renders HDR correctly. Multi-image HEIF requires a policy for primary image versus auxiliary/depth images; one file is not always one flat raster [9–11].

### RAW

Verified: rawpy offers wheels on Linux, macOS and Windows; its README documents that stable releases use a stable LibRaw version, exposes runtime LibRaw version/features and lists wheel feature differences. GPL demosaic packs are not included in its wheels. rawpy is MIT, while its bundled LibRaw license file is LGPL 2.1; source builds/features require their own review [12, 13]. The README also documents a Linux OpenMP/fork deadlock interaction and recommends spawn/forkserver [12].

**[INFERENCE]** Keep rawpy optional. Embedded preview extraction is an efficient browsing path when available; record that a preview rather than full sensor development was analyzed. Full development needs explicit white balance, gamma, color space, auto-brightness and bit-depth settings—otherwise comparisons can reflect processing defaults rather than photographic differences. Use a camera support matrix tied to the actual LibRaw version, not a promise based on filename extension. Cross-platform workers should use an explicit safe process-start policy rather than inheriting native thread pools through fork.

## 3. OpenCV and PySceneDetect

Verified: OpenCV’s VideoCapture property documentation cautions that actual property behavior depends on hardware, driver and API backend [16]. Its Python packaging README distinguishes MIT packaging scripts, Apache-2.0 OpenCV, bundled FFmpeg licensing, and Qt licensing in non-headless Linux wheels [16a]. PySceneDetect is BSD-3-Clause and provides OpenCV/PyAV backends plus multiple transition detectors [14, 14a].

Important current-version correction: the **0.7 migration guide** explicitly introduces VFR-aware PTS-backed timestamps for both OpenCV and PyAV backends. `FrameTimecode.pts` and `.time_base` retain timing; `.frame_num` is approximate for VFR. The current documentation shown during research identifies 0.7.1 and recommends pinning below the next major release [14, 15]. Old advice that every PySceneDetect release assumes CFR is no longer accurate. This remains documentation evidence, not a test of the wheel eventually selected for this project.

**[INFERENCE]** Use a headless OpenCV package if OpenCV is needed in a worker; install only one package variant supplying `cv2`. Prefer FFmpeg/PyAV timing evidence over treating an arbitrary OpenCV FPS/position property as authoritative. Pin the chosen PySceneDetect API/version and persist rational PTS values instead of converting through frame numbers. Detector thresholds, downscale and minimum scene lengths affect outcomes; flashing lights, fast camera motion, dissolves and low-light footage need representative evaluation. Scene splitting is a later export concern: do not mutate originals or equate candidate cuts with semantic scenes. CPU operation is the baseline here; GPU-enabled video decode does not imply that the Python scene detector itself becomes a GPU algorithm.

## 4. Speech recognition and diarization

Verified: faster-whisper is MIT and uses CTranslate2. It supports CPU int8 and NVIDIA GPU inference, optional word timestamps, VAD and batch transcription. It normally decodes through PyAV, which bundles FFmpeg libraries, and model-name loading downloads from Hugging Face [17]. CTranslate2 prebuilt CPU support includes x86-64 and ARM64; its GPU page lists NVIDIA, not a general Metal/ROCm backend. Current faster-whisper documentation specifies CUDA 12 cuBLAS and cuDNN 9 for its current GPU dependency path [18]. Upstream speed tables disclose specific hardware/settings; they are not portable performance commitments [17].

**[INFERENCE]** Offer CPU transcription as an optional profile and NVIDIA acceleration as separately resolved dependencies. Do not advertise faster-whisper GPU acceleration on Apple silicon merely because Python runs there. Alternative Apple speech runtimes can be evaluated separately; MLX’s repository includes a Whisper example but that does not verify a substitute pipeline’s timestamps, model coverage or parity [26]. Consume the faster-whisper segment generator to completion before marking a job complete; the README explicitly notes lazy execution [17]. VAD reduces irrelevant audio work but does not certify that remaining words were spoken. Word alignment estimates need separate quality evaluation from transcript accuracy.

Verified: pyannote.audio is MIT; Community-1 executes locally and requires acceptance and a Hugging Face token for acquisition. Public model metadata reports CC BY 4.0. The README distinguishes its premium remote pipeline from the local one and documents configurable telemetry including file duration and speaker-count settings; `PYANNOTE_METRICS_ENABLED=0` disables it [20, 21]. The current README also identifies torchcodec/FFmpeg decoding dependencies [20].

**[INFERENCE]** Diarization must remain a separate optional capability with its own model terms, download consent and privacy configuration. Explicitly disable metrics, load vetted local artifacts, and test offline behavior before claiming an offline profile. Do not substitute the premium hosted pipeline if the local model cannot be acquired. Maintain transcript and diarization evidence separately, then associate timed words/segments with speaker turns; do not invent identity or resolve overlap solely by choosing the nearest turn. An alignment library such as WhisperX is a separate pipeline with additional models/licenses, not a dependency needed merely to obtain faster-whisper’s native word timestamps [17].

## 5. CLIP/SigLIP and generative analysis

Verified: OpenAI CLIP is MIT; its model card describes image/text contrastive representations, English-focused evaluation, counting/fine-grained weaknesses, class-taxonomy sensitivity and bias. It warns against untested deployment and identifies surveillance/facial recognition as out of scope [22]. Google’s `siglip2-base-patch16-224` card is Apache-2.0 and explicitly supports zero-shot image classification, image-text retrieval and use as a vision encoder; the card supplies image-feature extraction through Transformers [23]. A vision encoder is not itself a caption-generating VLM.

**[INFERENCE]** Prefer a small, explicitly pinned SigLIP 2 checkpoint as the first candidate for local semantic retrieval, subject to in-domain evaluation; retain CLIP as an alternative rather than installing both by default. Store model and processor revision, embedding dimension, normalization and similarity method with vectors. Never mix embedding spaces between versions. Candidate-label scores and similarity distances should not become universal confidence percentages. Evaluate language and asset types actually expected. Neither model establishes facial identity, exact OCR, object counting, camera movement, shot quality or causal action understanding. A separate specialist model/pipeline would be needed before claiming those tasks.

**[INFERENCE]** Sampling video frames plus image embeddings can supply frame-level search candidates, but can miss a brief event between samples. Return sampled-frame times and coverage, not a claim that the whole interval contains the query. Full-video motion tasks require temporal evidence absent from an isolated still-image encoder.

### Runtime choices

| Runtime | Verified support | Recommended boundary [INFERENCE] |
| --- | --- | --- |
| Ollama | MIT runtime; local server defaults to `127.0.0.1:11434`; CPU/GPU/hybrid loading is documented. Current Ollama also has cloud models and web search; `OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud` enables local-only mode [24]. | Convenient user-managed optional provider. Require loopback, an explicitly local model and local-only mode for the private profile. Do not infer privacy from “Ollama” in a provider name or allow automatic cloud fallback. Model acquisition and auto-updates can still involve network access. |
| llama.cpp | MIT; C/C++ LLM/VLM inference, CPU quantization, Metal, CUDA, HIP, Vulkan and other backends, plus CPU/GPU hybrid operation and an API server [25]. | Useful optional runtime when controlled binary/model packaging matters. Pin a supported model/quantization and backend; GGUF or quantization does not change the original weight license. Hardware backend availability is not proof every model/operator is supported or fast. |
| MLX | MIT array framework with Python/C++/Swift APIs, lazy operations and CPU/GPU unified-memory model. Current README documents macOS install **and Linux CUDA/CPU packages**; includes LLM and Whisper examples [26]. | Strong Apple-silicon candidate, not the cross-platform foundation contract. Do not repeat outdated “MLX is Apple-only” as an absolute claim; Linux package availability still does not establish Windows support or parity for every MLX model application. |

**[INFERENCE]** Choose one initial optional generative provider rather than building three native stacks together. A text LLM can summarize transcript/metadata that is explicitly supplied; a compatible VLM can draft descriptions of supplied images. Require result provenance including model revision, prompt/schema revision and exact frames/text passed in. Structured JSON constrains syntax, not truth. Label generated captions, inferred emotions, aesthetics and summaries as interpretations, not objective metadata. Models have separate weight licenses, acceptable-use conditions and memory requirements even when every runtime above is MIT [22–26].

## 6. Optional cloud APIs: a separate, opt-in data route

Verified examples, not a provider benchmark:

- OpenAI documents hosted transcription, including a diarizing transcription model [27]. Its API data policy says API content is not used for training by default, but default abuse-monitoring logs can retain customer content for up to 30 days with exceptions; application-state retention is endpoint-specific and Zero Data Retention requires eligibility/approval [28]. “Not used for training” is not “never stored.”
- Gemini documents direct video input, description/question answering and timestamp references [29]. Its terms distinguish unpaid services (content can be used to improve products and reviewed by humans; do not submit sensitive/confidential/personal information) from paid services (no product-improvement use, but limited-period safety/security logging and other conditions). The terms also include geographic and professional/business-use restrictions [30]. A private personal-media product cannot assume all free or paid API use is contractually appropriate.

**[INFERENCE]** Keep hosted providers out of the default path and foundation. An optional adapter must disclose provider, endpoint/model, exactly which originals/frames/audio/transcripts/metadata leave the machine, region/retention assumptions, cost limits and any stored remote-file identifiers before upload. Sending a frame or transcript is still exporting personal data. Minimize the selected payload, strip incidental EXIF/location data from upload derivatives, and avoid logging credentials or media contents. Never silently fall back to cloud on missing GPUs, gated weights, model errors or timeouts. Provider-generated timestamps are analytical outputs, not a replacement for source PTS. Cloud service terms/retention and endpoint/model limits must be rechecked at integration time; no fixed upload size, sampling rate, price or accuracy promise is adopted here.

## 7. Python versus Rust, TypeScript and hybrid

| Choice | Evidence | Assessment [INFERENCE] |
| --- | --- | --- |
| Python core | faster-whisper, pyannote, rawpy, Pillow and PySceneDetect expose Python APIs; native libraries do much of the actual decoding/inference [7–20]. | Best initial integration surface and least translation glue for this workload. Native wheels/GPU dependencies still create platform work; Python is not a single-binary deployment solution by itself. Keep large pixel/audio tensors in native libraries and bounded workers, not Python per-pixel loops. |
| Rust core | FFmpeg/ExifTool CLIs and llama.cpp server boundaries are language-independent; libheif exposes a C API [1, 5, 11, 25]. | Good for a future shell, bounded file processing or a measured hot path. Replacing Python-first AI pipelines now means bindings, subprocess workers or different model implementations; it does not erase codec licensing, GPU drivers or weight distribution. No claim that Rust inference is intrinsically faster for the same native backend is justified. |
| TypeScript/Node core | ONNX Runtime provides official Node bindings and prebuilt CPU support across Windows/Linux/macOS x64/ARM64; its documented accelerator matrix is narrower and platform-specific [31]. | Feasible for UI/API integration and selected ONNX models, but not direct parity with the named Python pipelines. Exporting a model to ONNX and verifying preprocessing/operators/timestamps is work, not a free language switch. A browser-facing UI also needs an explicit local-file/worker bridge. |
| Hybrid | CLI/server and C-API component boundaries above already exist [1, 5, 11, 25, 31]. | Keep Python core first; add a TS UI or native shell only for an actual product need. Use versioned job/result contracts and artifact references across the boundary, not copies of full frames in JSON. Do not introduce Rust plus Node plus Python merely to call existing native code. |

**[INFERENCE]** Separate base dependencies from optional analysis extras and hardware-specific environments. Use a supported conservative Python minor version after checking the actual wheel set; this research does not establish that every package builds on the newest Python. A single project environment containing every CUDA/PyTorch/CTranslate2/MLX combination is not a maintainable default.

## 8. Distribution and privacy release gates

These are **[INFERENCE]** acceptance gates derived from the verified limitations above, not checks already run:

1. A base install scans/probes supported files without a model download, cloud key, FilmCraft installation, GPU or optional ML imports. Missing capabilities are named rather than replaced with fabricated results.
2. Maintain an exact software/binary/model inventory: versions, hashes/revisions, upstream sources, build flags, bundled libraries, notices, weight licenses, gates and attribution. Wrapper license, native library license, model license, hosted terms and codec patents are separate concerns.
3. Exercise Windows, macOS Intel/ARM and Linux targets actually claimed. Verify subprocess discovery, Unicode/long paths, optional-native-library failure, model cache location and GPU-driver mismatches. Package signing and redistribution remain unverified until platform-specific release work occurs.
4. Validate CFR/VFR, nonzero stream starts, audio/video offsets, rotated video, multi-audio files, absent metadata, corrupt media, HEIF multi-image files, RAW previews, ICC images and PQ/HLG derivatives before promising these capabilities. Use actual PTS/time base and record normalization. Current PySceneDetect documentation is not a substitute for those fixtures.
5. Verify offline execution after explicit dependency/model acquisition, with network access denied. Disable pyannote metrics and Ollama cloud modes. Audit SDK/runtime downloads, background updates and remote endpoints separately; “runs locally” is not a complete no-network guarantee.
6. Treat local thumbnails, transcripts, metadata/GPS, embeddings, speaker clusters, prompts and caches as sensitive artifacts. They remain data even when originals are never uploaded. Make their storage/deletion policy explicit; local-first is not automatically encrypted-at-rest or secure deletion.
7. Measure representative files on target hardware before setting worker concurrency, thread counts, memory bounds, model defaults, expected latency or quality claims. Upstream benchmark numbers are not this product’s measurements.

## Primary sources

All sources below were accessed during this research; mutable branch/documentation references should be replaced or supplemented by exact version references when selecting dependencies.

1. FFmpeg, [ffprobe documentation](https://ffmpeg.org/ffprobe.html).
2. FFmpeg, [ffmpeg CLI documentation](https://ffmpeg.org/ffmpeg.html), especially seeking, timestamp and `fps_mode` options.
3. FFmpeg, [filter documentation](https://ffmpeg.org/ffmpeg-filters.html), especially `tonemap`, `zscale`, `libplacebo`; [source documentation](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/filters.texi).
4. FFmpeg, [license and legal considerations](https://ffmpeg.org/legal.html).
5. ExifTool, [upstream README](https://github.com/exiftool/exiftool/blob/master/README).
6. ExifTool, [application documentation](https://exiftool.org/exiftool_pod.html).
6a. ExifTool, [QuickTime tags and date interpretation](https://exiftool.org/TagNames/QuickTime.html).
7. Pillow, [about/license](https://pillow.readthedocs.io/en/stable/about.html).
8. Pillow, [ImageCms reference](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html).
8a. Pillow, [image file formats](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html).
9. pillow-heif, [upstream README](https://github.com/bigcat88/pillow_heif/blob/master/README.md).
10. pillow-heif, [Pillow plugin limitations](https://pillow-heif.readthedocs.io/en/latest/pillow-plugin.html) and [installation/native build dependencies](https://pillow-heif.readthedocs.io/en/latest/installation.html).
11. pillow-heif, [wrapper license](https://github.com/bigcat88/pillow_heif/blob/master/LICENSE.txt); libheif, [README](https://github.com/strukturag/libheif), [COPYING](https://github.com/strukturag/libheif/blob/master/COPYING).
12. rawpy, [upstream README, wheels/features and limitations](https://github.com/letmaik/rawpy/blob/main/README.md).
13. rawpy, [bundled LibRaw license](https://github.com/letmaik/rawpy/blob/main/LICENSE.LibRaw).
14. PySceneDetect, [API/detectors/backends](https://www.scenedetect.com/docs/latest/api.html).
14a. PySceneDetect, [license](https://www.scenedetect.com/copyright).
15. PySceneDetect, [0.7 migration guide](https://www.scenedetect.com/docs/latest/api/migration_guide.html) and [timecode API](https://www.scenedetect.com/docs/latest/api/common.html).
16. OpenCV, [Video I/O properties and backend caveats](https://docs.opencv.org/4.13.0/d4/d15/group__videoio__flags__base.html).
16a. opencv-python, [packaging and licensing README](https://github.com/opencv/opencv-python), [third-party notices](https://github.com/opencv/opencv-python/blob/4.x/LICENSE-3RD-PARTY.txt).
17. faster-whisper, [upstream README](https://github.com/SYSTRAN/faster-whisper/blob/master/README.md) and [repository license metadata](https://github.com/SYSTRAN/faster-whisper).
18. CTranslate2, [prebuilt hardware support](https://opennmt.net/CTranslate2/hardware_support.html); faster-whisper GPU requirements in [17].
19. OpenAI Whisper, [model card and limitations](https://github.com/openai/whisper/blob/main/model-card.md).
20. pyannote.audio, [local versus hosted pipelines, acquisition and telemetry](https://github.com/pyannote/pyannote-audio).
21. pyannote Community-1, [public model metadata](https://huggingface.co/pyannote/speaker-diarization-community-1). Full raw model-card fetch was gated; see evidence limitations above.
22. OpenAI CLIP, [model card](https://github.com/openai/CLIP/blob/main/model-card.md) and [repository/license](https://github.com/openai/CLIP).
23. Google, [SigLIP 2 base model card](https://huggingface.co/google/siglip2-base-patch16-224/raw/main/README.md).
24. Ollama, [FAQ: networking, privacy, local-only configuration and model loading](https://docs.ollama.com/faq.md); [repository/license](https://github.com/ollama/ollama).
25. llama.cpp, [upstream README, hardware/backends and runtime license](https://github.com/ggml-org/llama.cpp/blob/master/README.md).
26. Apple MLX, [README and current platform installation paths](https://github.com/ml-explore/mlx/blob/main/README.md); [repository/license](https://github.com/ml-explore/mlx).
27. OpenAI, [speech-to-text guide](https://platform.openai.com/docs/guides/speech-to-text), [diarizing transcription model](https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize.md).
28. OpenAI, [API data controls](https://developers.openai.com/api/docs/guides/your-data.md).
29. Google, [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding).
30. Google, [Gemini API terms, including unpaid/paid data use and use restrictions](https://ai.google.dev/gemini-api/terms).
31. ONNX Runtime, [official Node.js bindings and platform/provider matrix](https://onnxruntime.ai/docs/get-started/with-javascript/node.html).
