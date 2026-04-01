#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="https-vpn-server"
CONTAINER_NAME="VPN"
VOLUME_NAME="vpn-data"

echo "[*] Building Docker image: $IMAGE_NAME"
docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

# Stop and remove existing container if running
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "[*] Removing existing container: $CONTAINER_NAME"
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
fi

echo "[*] Starting container: $CONTAINER_NAME"
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --cap-add NET_ADMIN \
    --device /dev/net/tun \
    --sysctl net.ipv4.ip_forward=1 \
    -p "${VPN_PORT:-443}:${VPN_PORT:-443}" \
    -v "$VOLUME_NAME":/etc/vpn \
    --log-opt max-size=10m --log-opt max-file=3 \
    ${VPN_SECRET:+-e VPN_SECRET="$VPN_SECRET"} \
    ${VPN_DOMAIN:+-e VPN_DOMAIN="$VPN_DOMAIN"} \
    ${VPN_PORT:+-e VPN_PORT="$VPN_PORT"} \
    ${VPN_HOST_IP:+-e VPN_HOST_IP="$VPN_HOST_IP"} \
    "$IMAGE_NAME"

echo "[*] Container started. Waiting for initialization..."
sleep 2

echo ""
echo "=============================="
echo "  docker logs $CONTAINER_NAME"
echo "=============================="
echo ""
docker logs "$CONTAINER_NAME"
