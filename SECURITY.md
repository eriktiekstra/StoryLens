# Security policy

## Current guarantees and limits

Default metadata/technical processing runs locally without inference/network requests. Explicit semantic analysis/search uses local CPU CLIP/SigLIP models; captions send bounded metadata-free RGB frames only to a numeric loopback Ollama origin. The adapter requires daemon cloud-disabled status, an installed local vision-capable model, pinned model digest/runtime version, and denies redirects/proxy environment routing. No automatic downloads, cloud fallback, credentials or telemetry are introduced. Trust the selected runtime/model and enforce OS egress restrictions for a stronger offline guarantee; a loopback daemon is not a sandbox.

Sources are read-only; no sidecars are written, and analysis lives separately in a local SQLite catalog/artifact tree. Catalogs/exports are not encrypted and can contain paths, GPS, model descriptions and embeddings. Restrict permissions and use encrypted disks as needed. Model captions are untrusted hypotheses and may hallucinate or contain prompt injection; never execute them as agent instructions.

FFmpeg/ffprobe run with argument arrays (no shell), a finite timeout and file/pipe protocols only. This blocks ordinary remote-protocol media references; **it is not a decoder sandbox or a universal no-network guarantee for arbitrary configured executables**. Configure only a trusted local ffprobe. Symlink entries are skipped, but a user-selected root is resolved; concurrent filesystem replacement and decoder bugs remain risks. Analyze untrusted media in an OS sandbox with read-only source mounts, restricted filesystem access, resource limits and network disabled. Metadata can be large or malformed. Pillow pixel limits remain enabled. Technical extraction bounds streamed/probe/error/JPEG payloads, source pixels, samples and scene count; metadata-only ffprobe capture is still not byte-capped. Derived scene JPEGs and signal results are sensitive too, stored in the local catalog-adjacent artifact tree.

Never expose the future MCP/HTTP API to untrusted clients without explicit allowed roots and authentication/transport controls. CLI path access follows the invoking user's OS permissions. It is not a multi-tenant service. No destructive command or remote listener exists in the current release.

## Future cloud policy

No cloud adapter is shipped. A future adapter must enforce explicit provider/task/data-class authorization, deny uploads by default, minimize frames, document retention/provider terms and never include credentials in logs/exports. Model acquisition is a separate deliberate permission. The current Ollama adapter requires OLLAMA_NO_CLOUD=1 (or equivalent daemon setting), verified via /api/status; missing/false status and remote/cloud model routing are rejected. This protects the application policy, not a malicious/reconfigured daemon or arbitrary executable. See integration setup and research notes.

## Reporting vulnerabilities

This new repository has no established private security address or supported-release policy yet. Use the hosting repository's private vulnerability reporting if enabled; otherwise contact a maintainer privately before providing exploit material. Do not upload family media, GPS, credentials or weaponized files to public issues. Public reports may describe the affected version and sanitized impact without private payloads. No response-time guarantee is claimed.
