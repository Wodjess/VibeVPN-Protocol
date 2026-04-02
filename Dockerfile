FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        iproute2 \
        iptables \
        openssl \
        procps \
        certbot \
        cron \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir websockets>=12.0

WORKDIR /app
COPY common.py tun_linux.py server.py users.py ./

# CLI tool for user management
RUN ln -s /app/users.py /usr/local/bin/vpn-users && \
    chmod +x /app/users.py

RUN mkdir -p /etc/vpn

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 443

ENTRYPOINT ["/entrypoint.sh"]
