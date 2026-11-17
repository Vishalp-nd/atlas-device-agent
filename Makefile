# Atlas Agent API — Docker build/run helpers.
#
# Usage:
#   make docker-build-run                    # local dev: build + run in foreground (Ctrl+C to stop)
#   make docker-build-run-detached            # hosting: build + run in background, survives SSH logout
#   make docker-build-run-detached PORT=9000  # same, on a different port
#   make docker-logs                          # tail logs of a detached run
#   make docker-stop                          # stop + remove the container (either mode)
#   make docker-limits                        # apply the CPU / memory caps to the running container, no restart
#
# Log Analyzer UI (static server, same image, different command; talks to ATLAS_API_URL from .env):
#   make docker-ui-run-detached               # run the UI in background on UI_PORT (default 8081)
#   make docker-ui-run                        # same, in foreground
#   make docker-ui-logs / docker-ui-stop
#
# The image contains only Python + dependencies. The actual app code, .env,
# db_credentials.ini, skills/, and prompts/ all come from bind-mounting
# this repo checkout into the container at runtime (see ./Dockerfile).

IMAGE_NAME := atlas-agent-api
PORT       ?= 8501
UI_PORT    ?= 8611
UI_NAME    := atlas-log-analyzer-ui
# The Log Analyzer rebuilds each log day in /dev/shm; Docker's default is too small
SHM_SIZE   ?= 8g
# CPU and memory caps for the API container (where the Log Analyzer runs), so a big run can't
# take the whole machine: CPU_LIMIT_PCT of the host's CPUs (--cpus) and MEM_LIMIT_PCT of its RAM
# (--memory, which also counts the container's /dev/shm; no swap on top). Past the memory cap the
# kernel kills a process inside the container instead of the host running out of memory.
# Or set CPUS / MEMORY directly (e.g. MEMORY=20g).
CPU_LIMIT_PCT ?= 90
MEM_LIMIT_PCT ?= 80
HOST_CPUS  := $(shell nproc 2>/dev/null || echo 1)
HOST_MEM_MB := $(shell awk '/^MemTotal:/ { printf "%d", $$2 / 1024 }' /proc/meminfo 2>/dev/null || echo 0)
CPUS       ?= $(shell awk 'BEGIN { printf "%.2f", $(HOST_CPUS) * $(CPU_LIMIT_PCT) / 100 }')
MEMORY     ?= $(shell awk 'BEGIN { printf "%dm", $(HOST_MEM_MB) * $(MEM_LIMIT_PCT) / 100 }')
LIMITS     := --cpus=$(CPUS) --memory=$(MEMORY) --memory-swap=$(MEMORY)
REPO_ROOT  := $(CURDIR)
ENV_FILE   := .env
GIT_BRANCH := $(shell git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
GIT_COMMIT := $(shell git rev-parse --verify HEAD 2>/dev/null || echo unknown)

# device-automation checkout (default: next to this repo). Mounted read-only at /device-automation,
# which is where the code looks inside the container (/repo/../device-automation); skipped if it doesn't exist.
DEVICE_AUTOMATION_HOST ?= $(abspath $(REPO_ROOT)/../device-automation)
DA_MOUNT   := $(if $(wildcard $(DEVICE_AUTOMATION_HOST)),-v $(DEVICE_AUTOMATION_HOST):/device-automation:ro,)

# Agent mode signs people in with DeviceQAI's sign-in (deviceqa_auth in a device-automation
# checkout): DEVICEQA_HOST is the checkout that has it (default: the one above; mounted apart,
# read-only, when it is another one). DQA_AUTH_DIR (the sign-in store) and DQA_TOKEN_KEY_FILE
# (encrypts Claude tokens) should be the ones the DeviceQAI platforms use, to share people and
# tokens with them; the key is mounted only if it exists, else one is made in the store.
DEVICEQA_HOST      ?= $(DEVICE_AUTOMATION_HOST)
DQA_AUTH_DIR       ?= $(HOME)/.deviceqa
DQA_TOKEN_KEY_FILE ?= $(HOME)/.deviceqa-token.key
AWS_DIR            ?= $(HOME)/.aws
AGENT_MOUNTS := \
	$(if $(filter $(abspath $(DEVICEQA_HOST)),$(abspath $(DEVICE_AUTOMATION_HOST))),-e DEVICEQA_ROOT=/device-automation,$(if $(wildcard $(DEVICEQA_HOST)/deviceqa_auth),-v $(abspath $(DEVICEQA_HOST)):/deviceqa:ro -e DEVICEQA_ROOT=/deviceqa,)) \
	-v $(DQA_AUTH_DIR):/deviceqa-auth -e DQA_AUTH_DIR=/deviceqa-auth \
	$(if $(wildcard $(DQA_TOKEN_KEY_FILE)),-v $(DQA_TOKEN_KEY_FILE):/deviceqa-token.key:ro -e DQA_TOKEN_KEY_FILE=/deviceqa-token.key,-e DQA_TOKEN_KEY_FILE=/deviceqa-auth/token.key)
# S3 credentials for the log downloads where there is no EC2 instance role (e.g. a laptop)
AWS_MOUNT := $(if $(wildcard $(AWS_DIR)),-v $(AWS_DIR):/root/.aws:ro,)

.PHONY: docker-build docker-run docker-run-detached docker-build-run docker-build-run-detached docker-logs docker-stop docker-limits \
	docker-ui-run docker-ui-run-detached docker-ui-logs docker-ui-stop

docker-build:
	docker build \
		--build-arg VCS_BRANCH="$(GIT_BRANCH)" \
		--build-arg VCS_REF="$(GIT_COMMIT)" \
		-t $(IMAGE_NAME) .

# Foreground — attached to your terminal, Ctrl+C stops it. Good for local dev:
# you see startup errors immediately. Dies if your SSH session disconnects.
docker-run:
	mkdir -p $(DQA_AUTH_DIR)
	docker run --rm -it \
		--name $(IMAGE_NAME) \
		--add-host=host.docker.internal:host-gateway \
		--shm-size=$(SHM_SIZE) \
		$(LIMITS) \
		-p $(PORT):8501 \
		-v $(REPO_ROOT):/repo \
		$(DA_MOUNT) $(AGENT_MOUNTS) $(AWS_MOUNT) \
		$(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),) \
		$(IMAGE_NAME)

# Detached — keeps running after you log out. --restart unless-stopped brings
# it back automatically on a crash or machine reboot (until you `make docker-stop`).
docker-run-detached:
	mkdir -p $(DQA_AUTH_DIR)
	docker run -d \
		--restart unless-stopped \
		--name $(IMAGE_NAME) \
		--add-host=host.docker.internal:host-gateway \
		--shm-size=$(SHM_SIZE) \
		$(LIMITS) \
		-p $(PORT):8501 \
		-v $(REPO_ROOT):/repo \
		$(DA_MOUNT) $(AGENT_MOUNTS) $(AWS_MOUNT) \
		$(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),) \
		$(IMAGE_NAME)

docker-build-run: docker-build docker-run

docker-build-run-detached: docker-build docker-run-detached

docker-logs:
	docker logs -f $(IMAGE_NAME)

docker-stop:
	-docker stop $(IMAGE_NAME)
	-docker rm $(IMAGE_NAME)

# The caps of a running container can be changed in place (kept across its restarts)
docker-limits:
	docker update $(LIMITS) $(IMAGE_NAME)

# Log Analyzer UI — reuses the atlas-agent-api image (run `make docker-build` first if it
# doesn't exist). Serves log_analyzer/ui/ with config.js generated from ATLAS_API_URL in .env;
# the browser must be able to reach that URL. No shm needed. The repo is mounted at /repo.
docker-ui-run:
	docker run --rm -it \
		--name $(UI_NAME) \
		--add-host=host.docker.internal:host-gateway \
		-p $(UI_PORT):$(UI_PORT) \
		-v $(REPO_ROOT):/repo \
		$(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),) \
		$(IMAGE_NAME) \
		python -m log_analyzer.serve_ui --host 0.0.0.0 --port $(UI_PORT)

docker-ui-run-detached:
	docker run -d \
		--restart unless-stopped \
		--name $(UI_NAME) \
		--add-host=host.docker.internal:host-gateway \
		-p $(UI_PORT):$(UI_PORT) \
		-v $(REPO_ROOT):/repo \
		$(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),) \
		$(IMAGE_NAME) \
		python -m log_analyzer.serve_ui --host 0.0.0.0 --port $(UI_PORT)

docker-ui-logs:
	docker logs -f $(UI_NAME)

docker-ui-stop:
	-docker stop $(UI_NAME)
	-docker rm $(UI_NAME)
