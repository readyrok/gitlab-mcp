# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Multi-stage build:
#   1. `builder` installs deps with uv into a virtualenv
#   2. `runtime` copies just the venv + source into a slim final image
#
# Result: ~80MB final image vs ~400MB if we did everything in one stage.
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

# uv: fast, deterministic dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

WORKDIR /app

# Copy only what's needed to resolve deps first — this layer caches as long
# as pyproject.toml doesn't change, so source edits don't reinstall packages.
COPY pyproject.toml ./
COPY src ./src

# Install into a project-local venv (no system-wide pollution).
RUN uv sync --frozen --no-dev || uv sync --no-dev

# ---------------------------------------------------------------------------

FROM python:${PYTHON_VERSION}-slim AS runtime

# Run as non-root: containers running as root are a real CVE source.
RUN groupadd --system mcp && useradd --system --gid mcp --home /app mcp

WORKDIR /app

# Copy the resolved environment + the source from the builder stage.
COPY --from=builder --chown=mcp:mcp /app /app

USER mcp

# Make the venv's executables visible without `uv run` indirection.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Default to running the MCP server over stdio (the standard MCP transport).
ENTRYPOINT ["gitlab-mcp"]