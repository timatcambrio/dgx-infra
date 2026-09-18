# Image for `kb-mcp` (brief §7.2, §9 S4): runs `kb serve --transport http` inside the
# compose network. Also used as a one-off for `kb index` (`docker compose run --rm kb-mcp
# kb index`) and `kb index --init` -- the image carries the whole `kb` CLI, not just the
# server.

FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY retrieval ./retrieval
COPY pipeline ./pipeline
COPY README.md ./

# --frozen: the lockfile is the source of truth, never resolved fresh in the image build.
# --no-dev: pytest/reportlab/python-docx (fixture-generation only) stay out of the image.
RUN uv sync --extra serve --frozen --no-dev

# Non-root: nothing in this image needs root once dependencies are installed. `uv run`
# still touches its own cache directory at container start even against an already-synced
# .venv, so give it a writable one rather than the read-only home `useradd` would default
# to under a bind-mounted /app.
ENV UV_CACHE_DIR=/tmp/uv-cache
RUN useradd --create-home --uid 10001 kb \
    && mkdir -p /tmp/uv-cache \
    && chown -R kb:kb /app /tmp/uv-cache
USER kb

# ENTRYPOINT is `uv run` (not `uv run kb`) so `docker compose run --rm kb-mcp kb index`
# (brief §7.2) works unmodified: compose appends "kb index" to the entrypoint, giving
# `uv run kb index`, rather than duplicating "kb" if the entrypoint already named it.
ENTRYPOINT ["uv", "run"]
CMD ["kb", "serve", "--transport", "http"]
