# Завершенные пункты roadmap Proxy Xray

Этот документ хранит уже реализованные пункты roadmap, чтобы основной план оставался списком будущих задач.

## 1. Разделить статус на понятные health-индикаторы

Завершено: 2026-06-07.

Общий статус не должен вводить в заблуждение, когда сломан только один компонент. Например, YouTube через SOCKS может работать быстро, но страница может показывать проблему со связью из-за другой проверки.

Реализованные индикаторы:

- процесс Xray;
- SOCKS proxy;
- HTTP proxy;
- LAN VLESS inbound;
- throughput активного proxy path;
- прямой интернет из контейнера;
- состояние подписки;
- RU DNS;
- global DNS;
- доступность Telegram API без отправки сообщения.

Это позволяет быстро понять, какой именно компонент сломан.

## 2. Более умный fallback scoring

Завершено: 2026-06-07.

Реализованная scoring-модель:

- extra-серверы получают базовый приоритет;
- живой subscription-сервер может обогнать упавший extra-сервер;
- медленные серверы получают штраф;
- недавно упавшие серверы получают штраф;
- старые успешные проверки постепенно теряют вес;
- тип транспорта продолжает влиять на приоритет;
- предпочтительный регион и измеренный throughput добавляют небольшие бонусы;
- для каждого кандидата в `/json` и таблицах статуса показываются `fallback_score` и причины расчета.

На выходе остается простой отсортированный список кандидатов, который используется для fallback и генерации Xray config.

## 3. Видимость и обновление GeoIP/Geosite assets

Завершено: 2026-06-07.

Реализовано:

- показывать размер и дату assets в статусе;
- копировать assets из образа в persistent runtime-директорию;
- обновлять LoyalSoldier `geoip.dat` и `geosite.dat` при старте и далее по расписанию;
- хранить время последней успешной загрузки и последнюю ошибку в `assets-state.json`;
- перезапускать Xray после успешной плановой замены assets;
- проверять persistent assets в smoke-тесте;
- не делать запуск сервиса зависимым от доступности GitHub.

Если GitHub недоступен, сервис не падает на старте.

## 4. Один runtime mode и удаление legacy-частей

Завершено: 2026-06-21.

Проект теперь сознательно заточен под один рабочий режим: домашний LAN-шлюз с VLESS-подпиской.

Реализовано:

- удалены старые `proxy-*.sh` генераторы одноразовых конфигов;
- удален `qrcode.sh`;
- `run.sh` упрощен до entrypoint для subscription supervisor;
- убраны старые режимы stdin/raw JSON;
- удалены неиспользуемые runtime-зависимости: dnsmasq, proxychains, qrencode;
- удалены build-time China dnsmasq lists и неиспользуемый `iran.dat`;
- smoke-тест LAN VLESS теперь использует реальный UUID из `.env`;
- проверены build, startup, status endpoint, SOCKS, HTTP, LAN VLESS, split DNS, RU direct routing и geo assets.

Так код и Docker image теперь соответствуют только тому режиму, который реально используется на домашнем сервере.

## 5. V2 pool mode без single-candidate runtime/API

Завершено: 2026-06-25.

После стабилизации v2 удалены остатки старой single-candidate модели вокруг active/standby slots.

Реализовано:

- active и hot standby pool теперь имеют дефолтный размер `3`;
- удалена ветка pool selection, которая при `size=1` принудительно держала текущий single candidate;
- standby pool больше не seed'ится отдельным single standby candidate;
- `/json` больше не публикует верхнеуровневые поля `fallback` и `standby`;
- status UI использует active backend, active pool, hot standby и observatory snapshots;
- удалена старая `/legacy` status page;
- smoke-тесты переведены на pool-based status fields.

Внутри Xray config остается native `fallbackTag`: это штатный fallback первого outbound внутри pool, а не отдельный старый runtime mode.
