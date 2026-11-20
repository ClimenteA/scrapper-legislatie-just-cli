.PHONY: start stop build logs

# Start both services (web app on localhost:8000 + indexer) in the background.
start:
	docker compose up -d

# Stop and remove the containers and the compose network. Host data is kept.
stop:
	docker compose down

# Build the image (OCR uses CPU-only torch; no nvidia/CUDA wheels).
build:
	docker compose build

# Follow the logs of both services (Ctrl-C to exit).
logs:
	docker compose logs -f
