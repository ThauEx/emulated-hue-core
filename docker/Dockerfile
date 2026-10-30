ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.10
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

# The base image already ships s6-overlay, bashio, curl, jq and tzdata, and sets
# PIP_BREAK_SYSTEM_PACKAGES plus Home Assistant's musllinux wheel index. The
# openssl binary is what the Entertainment service runs as its DTLS-PSK server.
COPY requirements.txt /tmp/requirements.txt
RUN \
    apk add --no-cache \
        openssl \
        python3 \
        py3-pip \
    \
    && apk add --no-cache --virtual .build-dependencies \
        build-base \
        git \
        libffi-dev \
        openssl-dev \
        python3-dev \
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
