# Atlas Agent API — Docker build/run helpers.
#
# Usage:
#   make docker-build-run                    # local dev: build + run in foreground (Ctrl+C to stop)
#   make docker-build-run-detached            # hosting: build + run in background, survives SSH logout
#   make docker-build-run-detached PORT=9000  # same, on a different port
#   make docker-logs                          # tail logs of a detached run
#   make docker-stop                          # stop + remove the container (either mode)
#
# The image contains only Python + dependencies. The actual app code, .env,
# db_credentials.ini, skills/, and prompts/ all come from bind-mounting
# this repo checkout into the container at runtime (see ./Dockerfile).

IMAGE_NAME := atlas-agent-api
PORT       ?= 8000
REPO_ROOT  := $(CURDIR)
ENV_FILE   := .env

.PHONY: docker-build docker-run docker-run-detached docker-build-run docker-build-run-detached docker-logs docker-stop

docker-build:
	docker build -t $(IMAGE_NAME) .

# Foreground — attached to your terminal, Ctrl+C stops it. Good for local dev:
# you see startup errors immediately. Dies if your SSH session disconnects.
docker-run:
	docker run --rm -it \
		--name $(IMAGE_NAME) \
		-p $(PORT):8000 \
		-v $(REPO_ROOT):/repo \
		$(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),) \
		$(IMAGE_NAME)

# Detached — keeps running after you log out. --restart unless-stopped brings
# it back automatically on a crash or machine reboot (until you `make docker-stop`).
docker-run-detached:
	docker run -d \
		--restart unless-stopped \
		--name $(IMAGE_NAME) \
		-p $(PORT):8000 \
		-v $(REPO_ROOT):/repo \
		$(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),) \
		$(IMAGE_NAME)

docker-build-run: docker-build docker-run

docker-build-run-detached: docker-build docker-run-detached

docker-logs:
	docker logs -f $(IMAGE_NAME)

docker-stop:
	-docker stop $(IMAGE_NAME)
	-docker rm $(IMAGE_NAME)
