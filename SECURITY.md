# Security policy

## Current guarantees and limits

M1/M2 run locally, makes no inference/network requests, downloads no models and sends no telemetry. It opens source files for reading, never writes sidecars, and stores analysis separately in a user-selected/local-default SQLite database. It does not encrypt metadata, paths, GPS, transcripts or embeddings at rest. Exports and errors may contain sensitive paths, tags and ffprobe diagnostics; share only deliberately. Restrict catalog/export permissions using your OS, use encrypted disks where needed, and do not put secrets in source filenames or ffprobe paths.

FFmpeg/ffprobe run with argument arrays (no shell), a finite timeout and file/pipe protocols only. This blocks ordinary remote-protocol media references; **it is not a decoder sandbox or a universal no-network guarantee for arbitrary configured executables**. Configure only a trusted local ffprobe. Symlink entries are skipped, but a user-selected root is resolved; concurrent filesystem replacement and decoder bugs remain risks. Analyze untrusted media in an OS sandbox with read-only source mounts, restricted filesystem access, resource limits and network disabled. Metadata can be large or malformed. Pillow pixel limits remain enabled. Technical extraction bounds streamed/probe/error/JPEG payloads, source pixels, samples and scene count; metadata-only ffprobe capture is still not byte-capped. Derived scene JPEGs and signal results are sensitive too, stored in the local catalog-adjacent artifact tree.

Never expose the future MCP/HTTP API to untrusted clients without explicit allowed roots and authentication/transport controls. CLI path access follows the invoking user's OS permissions. It is not a multi-tenant service. No destructive command or remote listener exists in the current release.

## Future cloud policy

No cloud adapter is shipped. An adapter must enforce explicit provider/task/data-class authorization, deny uploads by default, minimize representative frames, document retention/provider terms and never include credentials in logs or exports. Network-disabled inference must be enforced, not simulated by a local-looking model name. Model acquisition is a separate explicit permission. Ollama supports cloud modes; configure local-only behavior before any future integration. See the research notes.

## Reporting vulnerabilities

This new repository has no established private security address or supported-release policy yet. Use the hosting repository's private vulnerability reporting if enabled; otherwise contact a maintainer privately before providing exploit material. Do not upload family media, GPS, credentials or weaponized files to public issues. Public reports may describe the affected version and sanitized impact without private payloads. No response-time guarantee is claimed.
