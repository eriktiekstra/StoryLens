# Local/self-hosted recipe. Review Debian FFmpeg redistribution obligations
# before publishing images; this project does not publish a container registry.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ARG MCP=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/storylens \
    XDG_DATA_HOME=/state \
    XDG_CACHE_HOME=/state/cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 storylens \
    && useradd --uid 10001 --gid 10001 --create-home storylens \
    && mkdir -p /state /media \
    && chown storylens:storylens /state

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/storylens ./src/storylens
RUN case "$MCP" in \
      0) python -m pip install --no-cache-dir /build 'Pillow==12.3.0' 'numpy==2.5.4' ;; \
      1) python -m pip install --no-cache-dir '/build[mcp]' 'Pillow==12.3.0' 'numpy==2.5.4' ;; \
      *) echo 'MCP must be 0 or 1' >&2; exit 2 ;; \
    esac \
    && rm -rf /build

USER 10001:10001
WORKDIR /state
ENTRYPOINT ["storylens"]
CMD ["--help"]
