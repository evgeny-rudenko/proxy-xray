# DNS через VLESS: план и реализация

## Цель

Не-RU DNS-запросы не должны быть видны домашнему провайдеру и ТСПУ. RU DNS и маршрутизация российских ресурсов должны остаться прямыми. Функция должна отключаться одной настройкой без изменения proxy-конфигурации.

## Реализованные этапы

- [x] Добавлен параметр `DNS_GLOBAL_ROUTE=direct|socks|http`.
- [x] Global DNS-over-TCP может идти через активный SOCKS switch и VLESS-пул.
- [x] Добавлен альтернативный transport через HTTP `CONNECT` proxy.
- [x] RU-домены продолжают использовать прямые Yandex DNS.
- [x] Supervisor формирует bootstrap-список из URL подписки и VLESS endpoint.
- [x] Bootstrap-домены разрешаются напрямую, исключая циклическую зависимость запуска Xray.
- [x] До готовности proxy switch разрешён ограниченный startup-direct режим для redirect при первичной загрузке подписки.
- [x] Обычный global DNS не использует прямой fallback по умолчанию.
- [x] Добавлен положительный in-memory cache и stale cache для коротких переключений.
- [x] DNS transport и последний фактический route публикуются в JSON, diagnostics и Web UI.
- [x] Smoke test отдельно проверяет DNS, SOCKS, HTTP proxy и LAN VLESS.
- [x] `DNS_GLOBAL_ROUTE=direct` полностью возвращает прежний режим DNS.

## Потоки запросов

```text
.ru / .su / .рф -> split DNS -> Yandex DNS напрямую

обычный global -> split DNS -> SOCKS или HTTP switch -> active Xray slot
              -> VLESS outbound -> 8.8.8.8 / 1.1.1.1 TCP:53

subscription/VLESS endpoint -> split DNS -> bootstrap DNS-over-TCP напрямую
```

## Ограничения

- Кэш хранится в памяти и очищается при пересоздании контейнера.
- Во время полного отсутствия Xray новые global-домены без cache не разрешаются. Bootstrap DNS продолжает работать и позволяет supervisor восстановить VLESS.
- Режим `http` предусмотрен и тестируется на уровне transport-кода, но рекомендуемый runtime-режим — `socks`, поскольку он является основным внутренним transport проекта.
