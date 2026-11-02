# Atlas Device Agent image (atlas/, the FastAPI service).
#
# This image ships ONLY the Python runtime + installed dependencies — it does
# NOT contain the application code, .env, db_credentials.ini, skills/, or
# prompts/. Those all come from bind-mounting this repo checkout into the
# container at runtime (see the Makefile), so:
#   - secrets never end up baked into an image layer
#   - editing a skill or prompt doesn't require a rebuild

FROM python:3.13-slim

ARG VCS_BRANCH=unknown
ARG VCS_REF=unknown
LABEL org.opencontainers.image.ref.name="$VCS_BRANCH" \
	org.opencontainers.image.revision="$VCS_REF"

# 7z: the Log Analyzer reads downloaded log archives directly (lib/log_archive.py);
# the CLI is ~2-3x faster than its py7zr fallback
RUN apt-get update \
	&& apt-get install -y --no-install-recommends p7zip-full \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The real working directory at runtime is the bind-mounted repo root, not
# anything copied into the image — see the Makefile's `-v $(REPO_ROOT):/repo`.
WORKDIR /repo

EXPOSE 8501

CMD ["python", "-m", "atlas", "serve", "--host", "0.0.0.0", "--port", "8501"]
