# Multi-stage build.
#
# Dependencies are installed into a virtualenv in the builder stage and copied
# into a clean runtime image, so the compiler toolchain never ships to
# production. Requirements are copied before the source so that editing app
# code does not invalidate the (slow) dependency layer.

# Overridable so a network that cannot reach Docker Hub can point at an
# equivalent image on another registry (see PYTHON_IMAGE in .env.example).
ARG PYTHON_IMAGE=python:3.13-slim

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1


FROM base AS builder

# Only needed if a dependency has no manylinux wheel for this Python version;
# harmless otherwise, and absent from the runtime image either way.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt


FROM base AS runtime

ENV PATH="/opt/venv/bin:$PATH"

# Run as a non-root user. Nothing in the image needs write access.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser app ./app

COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# A named volume mounted here inherits this ownership, so the non-root user can
# write the generated development key.
RUN mkdir -p /var/lib/synapse && chown appuser:appuser /var/lib/synapse

USER appuser
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# No curl in the slim image; the interpreter is already here.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
