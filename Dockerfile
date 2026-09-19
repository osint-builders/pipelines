FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca
WORKDIR /builder
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra build --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --extra build --no-dev
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/builder/.venv/bin/pipelines"]
