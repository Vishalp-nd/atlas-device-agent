# Atlas Device Agent image (atlas/, the FastAPI service).
#
# This image ships ONLY the Python runtime + installed dependencies — it does
# NOT contain the application code, .env, db_credentials.ini, skills/, or
# prompts/. Those all come from bind-mounting this repo checkout into the
# container at runtime (see the Makefile), so:
#   - secrets never end up baked into an image layer
#   - editing a skill or prompt doesn't require a rebuild

FROM python:3.11-slim

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The real working directory at runtime is the bind-mounted repo root, not
# anything copied into the image — see the Makefile's `-v $(REPO_ROOT):/repo`.
WORKDIR /repo

EXPOSE 8000

CMD ["python", "-m", "atlas", "serve", "--host", "0.0.0.0", "--port", "8000"]
