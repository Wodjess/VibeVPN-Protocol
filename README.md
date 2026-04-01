# HTTPS VPN Tunnel

VPN-туннель поверх WebSocket/HTTPS. Весь трафик выглядит как обычное HTTPS-соединение.

```
[macOS Client] ──WSS (port 443)──> [Linux VPS / Docker] ──> Internet
     utun                              tun0 + NAT
```

## Структура

```
├── Server/
│   ├── Compile.sh       # Сборка и запуск Docker-контейнера
│   ├── Dockerfile
│   ├── entrypoint.sh    # Генерация секрета, сертификатов, вывод инфо
│   ├── server.py        # VPN-сервер (мульти-клиент)
│   ├── common.py        # Общие утилиты
│   └── tun_linux.py     # TUN-интерфейс Linux
│
├── Client/
│   ├── gui.py           # macOS GUI-приложение
│   ├── client.py        # VPN-клиент (CLI)
│   ├── common.py        # Общие утилиты
│   ├── tun_darwin.py    # TUN-интерфейс macOS (utun)
│   └── requirements.txt
```

## Сервер (Docker)

### Быстрый старт

```bash
cd Server
chmod +x Compile.sh
./Compile.sh
```

После запуска смотрим данные для подключения:

```bash
docker logs VPN
```

### Опциональные параметры

```bash
# Свой секрет (иначе сгенерируется автоматически)
VPN_SECRET="my-secret" ./Compile.sh

# Свой домен для TLS-сертификата
VPN_DOMAIN="vpn.example.com" ./Compile.sh

# Свой порт
VPN_PORT=8443 ./Compile.sh
```

### Свои TLS-сертификаты (Let's Encrypt)

```bash
docker run -d --name VPN \
    --cap-add NET_ADMIN --device /dev/net/tun \
    --sysctl net.ipv4.ip_forward=1 \
    -p 443:443 \
    -v /etc/letsencrypt/live/example.com/fullchain.pem:/etc/vpn/cert.pem:ro \
    -v /etc/letsencrypt/live/example.com/privkey.pem:/etc/vpn/key.pem:ro \
    -e VPN_SECRET="your-secret" \
    https-vpn-server
```

## Клиент (macOS)

### Установка

```bash
cd Client
pip install -r requirements.txt
```

### GUI

```bash
python3 gui.py
```

Запросит пароль администратора через системный диалог macOS.

### CLI

```bash
sudo python3 client.py --server <IP> --port 443 --secret "<SECRET>"
```

Данные для подключения берём из `docker logs VPN`.
