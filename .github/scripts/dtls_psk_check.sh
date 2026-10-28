#!/bin/sh
# Mirrors emulated_hue/controllers/entertainment.py: openssl s_server -dtls
# with a PSK identity/key, -nocert, -quiet, payload read from stdout. A client
# (what the TV does) connects with the same identity and key and sends a frame.
set -u
IDENTITY="00112233-4455-6677-8899-aabbccddeeff"
KEY="00112233445566778899aabbccddeeff"
OUT=/tmp/server.out

openssl version
# stdin stays open without data, like the asyncio PIPE in entertainment.py.
sleep 30 | openssl s_server -dtls -accept 2100 -nocert \
    -psk_identity "$IDENTITY" -psk "$KEY" -quiet > "$OUT" 2>/tmp/server.err &
SERVER=$!
sleep 2

# The TV negotiates DTLS 1.2 with a PSK AES-GCM cipher.
printf 'HueStream-test-frame\n' | openssl s_client -dtls1_2 -connect 127.0.0.1:2100 \
    -psk_identity "$IDENTITY" -psk "$KEY" -cipher PSK-AES128-GCM-SHA256 \
    -quiet > /tmp/client.out 2>/tmp/client.err &
CLIENT=$!
sleep 3
kill "$CLIENT" 2>/dev/null
pkill -f "s_server" 2>/dev/null
pkill -f "sleep 30" 2>/dev/null

echo "--- server stdout"; cat "$OUT"
echo "--- server stderr"; cat /tmp/server.err
echo "--- client stderr"; cat /tmp/client.err
grep -q "HueStream-test-frame" "$OUT" && echo "GATE PASSED" || { echo "GATE FAILED"; exit 1; }
