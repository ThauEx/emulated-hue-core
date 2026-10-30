ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.10
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

# The base image already ships s6-overlay, bashio, curl, jq and tzdata, and sets
# PIP_BREAK_SYSTEM_PACKAGES plus Home Assistant's musllinux wheel index. The
# openssl binary is what the Entertainment service runs as its DTLS-PSK server.
# The apk versions are pinned for reproducible builds (like the base image's own
# pins); a new Alpine release drops the old ones, so bump them with the base image.
COPY requirements.txt /tmp/requirements.txt
RUN \
    apk add --no-cache \
        openssl=3.5.9-r0 \
        py3-pip=26.1.2-r0 \
        python3=3.14.10-r1 \
    \
    && apk add --no-cache --virtual .build-dependencies \
        build-base=0.5-r4 \
        git=2.54.0-r0 \
        libffi-dev=3.5.2-r1 \
        openssl-dev=3.5.9-r0 \
        python3-dev=3.14.10-r1 \
    \
    && pip install -r /tmp/requirements.txt \
    \
    && apk del --no-cache --purge .build-dependencies \
    && rm -f -r /tmp/* /root/.cache

# Copy root filesystem
COPY --chmod=0755 docker/rootfs /

# Copy app
COPY emulated_hue emulated_hue

# Build arguments
ARG HASS_ARCH
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.version=${BUILD_VERSION} \
    io.hass.name="Hass Emulated Hue" \
    io.hass.description="Hue Emulation for Home Assistant" \
    io.hass.arch="${HASS_ARCH}" \
    io.hass.type="addon"
