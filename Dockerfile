# Futures EV research platform + overnight edge live-validation harness.
# Matches the project's Python 3.14 environment. Licensed Databento data
# (cache/) and generated model bundles (bundles/) are NOT baked into the
# image -- they are bind-mounted at runtime via docker-compose.
FROM python:3.14-slim

# Non-interactive, unbuffered logs (important for the long-running live runner)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

# Build tools kept available in case a dependency lacks a cp314 wheel and
# must compile from source; removed in the same layer to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first for layer caching (includes pytest via requirements-dev).
COPY requirements.txt requirements-dev.txt ./
RUN pip install --upgrade pip && pip install -r requirements-dev.txt

# Then the source (data cache / bundles come in as volumes, not copied).
COPY mnq_system/ ./mnq_system/
COPY live_validation/ ./live_validation/
COPY tests/ ./tests/
COPY docs/ ./docs/
COPY README.md ./

# Default: show the research CLI help. Compose services override this.
CMD ["python", "-m", "mnq_system", "--help"]
