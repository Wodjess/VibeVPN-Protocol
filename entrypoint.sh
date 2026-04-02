#!/bin/bash
set -e

CERT_DIR="/etc/vpn"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"
USERS_FILE="$CERT_DIR/users.json"
VPN_PORT="${VPN_PORT:-443}"

# ── Create default admin user if no users exist ───────────────────────────
if [ ! -f "$USERS_FILE" ] || [ "$(cat "$USERS_FILE" 2>/dev/null)" = "{}" ]; then
    DEFAULT_USER="${VPN_ADMIN_USER:-admin}"
    DEFAULT_PASS="${VPN_ADMIN_PASS:-$(openssl rand -hex 8)}"
    # Pass password via env to avoid leaking in /proc/cmdline or ps
    VPN_STDIN_PASS="$DEFAULT_PASS" python3 /app/users.py add-stdin "$DEFAULT_USER"
    echo "[INIT] Created default user: $DEFAULT_USER / $DEFAULT_PASS"
    echo "$DEFAULT_PASS" > "$CERT_DIR/.admin_pass"
    chmod 600 "$CERT_DIR/.admin_pass"
fi

# ── TLS Certificate ───────────────────────────────────────────────────────
if [ "$VPN_LETSENCRYPT" = "1" ] && [ -n "$VPN_DOMAIN" ]; then
    # Let's Encrypt automatic certificate
    LE_DIR="/etc/letsencrypt/live/$VPN_DOMAIN"

    if [ ! -f "$LE_DIR/fullchain.pem" ]; then
        echo "[INIT] Obtaining Let's Encrypt certificate for $VPN_DOMAIN..."

        # Temporarily free port 443 for certbot standalone
        certbot certonly --standalone --non-interactive --agree-tos \
            --email "${VPN_EMAIL:-admin@$VPN_DOMAIN}" \
            -d "$VPN_DOMAIN" \
            --preferred-challenges http \
            ${VPN_PORT:+--http-01-port 80} \
            2>&1 | tail -5

        if [ -f "$LE_DIR/fullchain.pem" ]; then
            echo "[INIT] Let's Encrypt certificate obtained successfully"
        else
            echo "[INIT] WARNING: Let's Encrypt failed, falling back to self-signed"
        fi
    fi

    if [ -f "$LE_DIR/fullchain.pem" ]; then
        # Symlink LE certs to our cert paths
        ln -sf "$LE_DIR/fullchain.pem" "$CERT_FILE"
        ln -sf "$LE_DIR/privkey.pem" "$KEY_FILE"
        echo "[INIT] Using Let's Encrypt certificate for: $VPN_DOMAIN"

        # Setup auto-renewal cron (runs twice daily)
        echo "0 3,15 * * * certbot renew --quiet && ln -sf $LE_DIR/fullchain.pem $CERT_FILE && ln -sf $LE_DIR/privkey.pem $KEY_FILE" | crontab -
        cron
        echo "[INIT] Auto-renewal cron configured"
    fi
fi

# Fall back to self-signed if no cert exists yet
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "[INIT] No TLS certificate found, generating self-signed..."

    CERT_DOMAIN="${VPN_DOMAIN:-vpn.local}"
    SAN="DNS:$CERT_DOMAIN"
    if [ -n "$VPN_HOST_IP" ]; then
        SAN="$SAN,IP:$VPN_HOST_IP"
    fi

    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE" \
        -days 3650 \
        -subj "/CN=$CERT_DOMAIN" \
        -addext "subjectAltName=$SAN" \
        2>/dev/null

    chmod 600 "$KEY_FILE"
    echo "[INIT] Self-signed certificate generated for: $CERT_DOMAIN"
    echo "[INIT] NOTE: Clients will need --insecure flag with self-signed certs"
fi

# ── Detect TLS type ───────────────────────────────────────────────────────
if readlink "$CERT_FILE" 2>/dev/null | grep -q letsencrypt; then
    TLS_TYPE="Let's Encrypt (auto-renew)"
else
    TLS_TYPE="Self-signed"
fi

# ── Detect external IP ────────────────────────────────────────────────────
if [ -n "$VPN_HOST_IP" ]; then
    EXTERNAL_IP="$VPN_HOST_IP"
else
    EXTERNAL_IP="<SET VPN_HOST_IP env var>"
fi

# ── Print connection info ─────────────────────────────────────────────────
W=80
line()  { printf "║  %-${W}s║\n" "$1"; }
sep()   { printf "╠"; printf '═%.0s' $(seq 1 $((W+2))); printf "╣\n"; }
empty() { printf "║  %-${W}s║\n" ""; }

printf "╔"; printf '═%.0s' $(seq 1 $((W+2))); printf "╗\n"
printf "║  %-${W}s║\n" "HTTPS VPN Server"
sep
empty
line "Status:       RUNNING"
line "TLS:          $TLS_TYPE"
empty
sep
line "Connection settings:"
sep
empty
line "Server:       $EXTERNAL_IP"
line "Port:         $VPN_PORT"
empty
sep
line "Users:"
sep
empty
python3 /app/users.py list 2>/dev/null | while IFS= read -r u; do
    line "$u"
done
empty
sep
line "User management (run inside container):"
sep
empty
line "docker exec VPN vpn-users add <username> <password>"
line "docker exec VPN vpn-users remove <username>"
line "docker exec VPN vpn-users disable <username>"
line "docker exec VPN vpn-users enable <username>"
line "docker exec VPN vpn-users passwd <username> <password>"
line "docker exec VPN vpn-users list"
empty
sep
line "Max clients:  253"
line "Subnet:       10.8.0.0/24"
line "TLS cert:     $CERT_FILE"
empty
printf "╚"; printf '═%.0s' $(seq 1 $((W+2))); printf "╝\n"
echo ""

# ── Start the VPN server ──────────────────────────────────────────────────
exec python3 /app/server.py
