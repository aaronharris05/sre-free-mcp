# sre-free-mcp container image.
#
# Single image, two entry points:
#   - sre-runner          → Cloud Run job (scheduled task dispatcher)
#   - sre-mcp-server      → Cloud Run service (MCP HTTP/SSE)
#
# The CMD picks sre-mcp-server by default; Cloud Run jobs override
# CMD with `sre-runner --task=<name>` at deploy time.

FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --upgrade pip && \
    pip wheel --wheel-dir /wheels .

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SRE_CONFIG_DIR=/app/config \
    MCP_TRANSPORT=sse \
    PORT=8080

WORKDIR /app

COPY --from=build /wheels /wheels
RUN pip install --no-index --find-links /wheels sre-free-mcp && \
    rm -rf /wheels

# Bundle the example configs so the container boots even if the
# operator forgets to mount custom ones. Production deploys overlay
# their own /app/config via a Cloud Run volume / Secret Manager mount.
COPY config /app/config-examples
RUN cp -r /app/config-examples /app/config

# Non-root for Cloud Run's default execution context.
RUN useradd --uid 1001 --create-home --shell /usr/sbin/nologin sre && \
    chown -R sre:sre /app
USER sre

EXPOSE 8080

# Default to the MCP server; jobs override with sre-runner --task=<name>.
CMD ["sre-mcp-server"]
