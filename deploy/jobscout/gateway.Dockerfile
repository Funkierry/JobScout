# Wrap the browser-enabled backend image built and tested by source Compose.
ARG BACKEND_IMAGE=jobscout-gateway:latest
FROM ${BACKEND_IMAGE}
COPY skills/public /app/skills/public
COPY LICENSE /app/LICENSE
