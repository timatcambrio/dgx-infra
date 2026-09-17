# Image for `kb-mcp` (brief §7.2). Minimal now; nothing runs it before S4 -- S0 through S3
# run `kb` from the host venv against the compose `dev` profile's published `db` port. Not
# built or exercised by S0's verification.

FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY retrieval ./retrieval
COPY pipeline ./pipeline
COPY README.md ./

RUN uv sync --extra serve --frozen --no-dev

ENTRYPOINT ["uv", "run", "kb"]
