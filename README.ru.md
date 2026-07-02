# proxy-xray

[![Telegram](https://img.shields.io/badge/Telegram-@endominion-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/endominion)

Dockerized Xray gateway для домашней локальной сети.

Проект запускает Xray-Core за стабильными локальными портами, берет VLESS-серверы из подписки и локального extra-списка, держит active pool и hot standby, проверяет деградацию, переключается с плохих путей, показывает статус в Web UI и отправляет российские домены/IP напрямую.

## Что умеет

- SOCKS proxy на `1080`.
- HTTP proxy на `8123`.
- Локальный LAN VLESS inbound на `10086`.
- Web UI на `18080`.
- Обновление VLESS-подписки раз в два часа.
- Локальный список личных VLESS-ссылок в `vless-extra.txt`.
- Active Xray slot плюс hot standby slot.
- Проверки liveness, latency, small quality download, throughput и случайных кандидатов.
- Split DNS и прямой routing для `.ru`, `.su`, `.рф`, `geosite:category-ru`, `geoip:ru`.
- Runtime-обновление LoyalSoldier `geoip.dat` / `geosite.dat`.
- Telegram-уведомление после успешного переключения.
- Скрипт деплоя на домашний сервер по SSH.

## Безопасность репозитория

Реальные данные подключений не должны попадать в git.

Локальные файлы, которые игнорируются:

- `.env` - URL подписки, Telegram token/chat id, UUID для LAN VLESS;
- `vless-extra.txt` - личные VLESS-ссылки;
- `state.json` - cache подписки и накопленная статистика серверов;
- `vless-lan-qr.png` - локальный QR;
- `assets/` - скачанные geo assets.

Создание локальных файлов:

```shell
cp .env.example .env
cp vless-extra.example.txt vless-extra.txt
cp state.example.json state.json
mkdir -p assets
```

После этого нужно отредактировать `.env` и `vless-extra.txt`.

## Настройка `.env`

```shell
# VLESS subscription URL от провайдера.
XRAY_SUB_URL=https://example.com/subscription

# UUID для локальных LAN-клиентов, которые подключаются к этому gateway на порт 10086.
# Сгенерировать один раз: python3 -c 'import uuid; print(uuid.uuid4())'
# Этот же UUID нужно указать в V2RayTun / VLESS client config.
INBOUND_VLESS_ID=00000000-0000-0000-0000-000000000000

# Необязательные Telegram-уведомления.
# TELEGRAM_BOT_TOKEN: создать бота через @BotFather и взять HTTP API token.
# TELEGRAM_CHAT_ID: написать любое сообщение боту, затем выполнить:
# curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates"
# Взять message.chat.id из ответа.
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

TZ=Europe/Moscow
```

`INBOUND_VLESS_ID` не выдает провайдер подписки. Это ваш локальный UUID для входящего VLESS на домашнем gateway. Сгенерируйте его один раз, сохраните в `.env` и используйте этот же UUID в клиентском профиле.

Telegram-уведомления опциональны. Если они не нужны, оставьте `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` пустыми.

`vless-extra.txt` содержит по одной личной VLESS-ссылке на строку. Эти серверы не обновляются из подписки и проверяются чаще, чем subscription-кандидаты.

## Запуск

```shell
docker compose build proxy-xray
docker compose up -d --force-recreate
```

Проверка:

```shell
curl http://127.0.0.1:18080/json
docker logs proxy-xray --tail 80
```

Web UI:

```text
http://127.0.0.1:18080/
```

## Порты

| Порт | Назначение |
| --- | --- |
| `1080/tcp` | SOCKS proxy |
| `1080/udp` | SOCKS UDP |
| `8123/tcp` | HTTP proxy |
| `10086/tcp` | LAN VLESS inbound |
| `18080/tcp` | Web UI |

DNS relay есть внутри контейнера, но compose по умолчанию не публикует порт `53`.

## Подключение LAN VLESS клиента

LAN-клиенты могут подключаться к gateway через локальный VLESS inbound на порт `10086`.

Самый простой способ получить профиль клиента:

1. Откройте Web UI по LAN-IP сервера, а не через `127.0.0.1`:

   ```text
   http://HOME_SERVER_IP:18080/
   ```

2. Нажмите кнопку `Q` в правом верхнем углу.
3. Откроется страница `/client`.
4. Отсканируйте QR-код из V2RayTun или скопируйте строку подключения вручную.

Страница строит VLESS URL из адреса, по которому открыт Web UI. Например, если UI открыт как `http://192.168.2.200:18080/`, клиентская ссылка будет указывать на `192.168.2.200:10086`.

Ручной формат:

```text
vless://INBOUND_VLESS_ID@HOME_SERVER_IP:10086?security=none&type=tcp#home-proxy
```

Где:

- `INBOUND_VLESS_ID` - UUID из `.env`;
- `HOME_SERVER_IP` - LAN-IP Docker host.

## Как работает active pool и failover

Supervisor запускает два Xray-процесса:

- active slot принимает публичные `1080`, `8123`, `10086` через локальный TCP switch и содержит небольшой Xray balancer pool;
- standby slot уже запущен отдельно, содержит hot standby pool и проверяется в фоне.

Дефолтный compose:

- active pool: `3` кандидата;
- standby pool: `3` кандидата;
- extra reserve: `1` live private extra URI на active/standby slot, если есть;
- hot standby fast switch: `1` полный fail active path, если standby уже healthy;
- liveness check: каждые `20` секунд, failover после `2` ошибок;
- quality download: каждые `60` секунд, 512 KB, failover после `2` медленных проверок;
- heavy throughput: каждые `300` секунд, используется как quality metric.

Внутри каждого slot есть score-ordered pool. Xray выбирает outbound внутри active pool по native balancer/observatory. Первый outbound остается native Xray fallback, если observatory не может выбрать лучший.

Важное поведение соединений:

- Xray выбирает outbound из active pool для новых соединений.
- Xray не переносит уже открытое TCP-соединение на другой outbound.
- Если текущий outbound умер или деградировал, новые запросы браузера/приложения могут пойти через другой outbound внутри того же active pool.
- Уже открытые загрузки, медиапотоки или загрузки картинок могут на короткое время зависнуть или оборваться, потому что они были открыты через старый outbound.
- Если весь active slot стал плохим, supervisor переключает публичные порты на hot standby slot.

Поэтому короткие провалы связи во время смерти сервера возможны. Цель проекта - быстро восстановить новые соединения, а не гарантировать выживание каждого уже открытого TCP-сеанса.

Failover может сработать из-за:

- повторных health-check ошибок;
- повторной высокой latency;
- повторной низкой скорости small quality download;
- плохого active slot при готовом hot standby.

При успешном переключении:

1. Публичные порты переводятся на standby slot.
2. Предыдущий active pool head отправляется в soft quarantine.
3. Собирается новый standby.
4. Обновляется `state.json`.
5. Отправляется Telegram-уведомление, если настроено.

Проверки кандидатов идут последовательно: один случайный кандидат раз в 2-5 минут. Extra-серверы имеют больший вес. Это сделано, чтобы не долбить подписку большим количеством одновременных VLESS-подключений.

При старте active slot должен пройти preflight health check до подключения публичных портов. Если первый outbound в pool мертв, он отправляется в soft quarantine и пробуется другой pool.

### Ручной Extra Pool Override

В верхней панели основного dashboard есть кнопка `Extra pool`. Это аварийный одноразовый override для ситуации, когда подписочные серверы массово деградируют, а приватные extra-соединения работают лучше.

После клика и подтверждения supervisor сначала поднимает все `extra` VLESS-ссылки из `vless-extra.txt` в hot standby slot, немного ждет, затем проверяет через этот staged slot health и small quality download. Только если staged extra pool прошел проверки, локальные публичные порты переключаются на него. Для этой одной пересборки специально игнорируются обычный размер `active pool: 3` и ограничения по одному хосту.

Дефолтная конфигурация не меняется, а штатные health checks, failover, quarantine и пересборка standby продолжают работать дальше. Для подписки это безопасно, потому что active pool уходит с подписочных кандидатов на extra-ссылки. Но уже открытые TCP-сессии могут кратко оборваться в момент финального переключения.

## State file

`state.json` использует schema v2 и хранит cache кандидатов плюс bounded quality history:

- last OK/fail timestamps;
- last latency и throughput;
- последние `50` recent checks на кандидата;
- rolling success rate, failure streak, latency EWMA, throughput EWMA.

Файл пишется atomic. Если JSON поврежден, startup переименует его в `state.json.corrupt.<timestamp>` и продолжит работу с пустым state.

## Routing и DNS

Дефолтный compose отправляет российские ресурсы напрямую:

- `geosite:category-ru`
- `regexp:.*\.ru$`
- `regexp:.*\.su$`
- `regexp:.*\.xn--p1ai$`
- `geoip:ru`

Split DNS:

- RU-домены используют `77.88.8.8,77.88.8.1`;
- остальные домены используют `8.8.8.8,1.1.1.1`;
- upstream DNS опрашивается напрямую, не через Xray.

Xray sniffing включен на SOCKS, HTTP и LAN VLESS inbound с `routeOnly`, поэтому TLS SNI / HTTP Host используются для routing без перезаписи destination.

## Geo assets

LoyalSoldier assets лежат в `./assets`:

- `geoip.dat`
- `geosite.dat`
- `assets-state.json`

Образ содержит seed assets. Runtime refresh выполняется по расписанию, но compose использует `--no-asset-refresh-on-start`, чтобы медленная загрузка с GitHub не блокировала старт контейнера.

## Web UI

Endpoints:

- `/` - основной operator dashboard;
- `/status` - alias основного dashboard;
- `/dashboard-v5` - alias основного dashboard, оставлен для тестовых ссылок и закладок;
- `/dashboard-classic` - предыдущая компоновка dashboard, оставлена для сравнения;
- `/client` - LAN VLESS строка подключения и QR-код;
- `/servers/live` - протестированные живые серверы;
- `/servers/all` - все кандидаты;
- `/json` - machine-readable status;
- `/fragments/dashboard-v5` - небольшие HTML-фрагменты для обновления основного dashboard без перезагрузки страницы;
- `/fragments/status` - небольшие HTML-фрагменты для classic dashboard;
- `/control/force-extra-pool` - локальный POST action для аварийной кнопки `Extra pool`;
- `/diagnostics` - live direct/SOCKS/HTTP URL probes и DNS probes;
- `/diagnostics.json` - sanitized diagnostics JSON;
- `/diagnostics/bundle` - скачиваемый sanitized diagnostics JSON;
- `/logs` - последние логи supervisor.

Dashboard не использует полную auto-refresh перезагрузку страницы. Динамические блоки обновляются на месте каждые 15 секунд.

Скриншоты в README используют синтетические demo data. Имена серверов, endpoints, IDs и operational details не реальные.

![proxy-xray status dashboard](docs/status-dashboard.jpg)

![proxy-xray live servers](docs/status-live-servers.jpg)

Dashboard показывает:

- health indicators;
- текущее соединение;
- hot standby;
- active path, выбранный Xray balancer API;
- active/standby observatory snapshots в `/json`;
- score кандидатов;
- throughput;
- состояние подписки;
- состояние geo assets;
- diagnostics entry point;
- последние логи.

## Деплой на домашний сервер

Deploy script синхронизирует файлы по SSH, сохраняет runtime state на сервере, пересобирает image и пересоздает контейнер.

```shell
DEPLOY_HOST=192.168.1.10 \
DEPLOY_USER=user \
DEPLOY_PATH=/home/user/proxy-xray \
scripts/deploy-server.sh
```

По умолчанию копируются локальные `.env` и `vless-extra.txt`, но server-side `state.json` и `assets/` сохраняются.

См. [DEPLOY.md](DEPLOY.md).

## Smoke tests

Запуск client smoke container:

```shell
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm proxy-client-test
```

Проверяется:

- Web UI;
- SOCKS proxy;
- HTTP proxy;
- LAN VLESS inbound;
- basic throughput;
- small quality download status;
- split DNS behavior;
- RU direct-routing smoke access;
- bundled LoyalSoldier assets.
