# proxy-xray UI design variants

Эта папка хранит черновые варианты интерфейса, чтобы не потерять идеи по дальнейшей эволюции Web UI.

Все изображения используют синтетические данные: серверы, адреса, задержки и события не реальные.

## Варианты

| Вариант | Файл | Идея | Чем полезен |
| --- | --- | --- | --- |
| 1. Executive Overview | [variant-1-executive-overview.svg](variant-1-executive-overview.svg) | Спокойный обзор состояния сверху вниз. | Хорош для быстрого ответа на вопрос "все ли работает". |
| 2. Network Map | [variant-2-network-map.svg](variant-2-network-map.svg) | Визуальная схема маршрута: клиенты, proxy, active/hot pool, DNS, direct RU. | Помогает понимать, куда идет трафик и где может быть проблема. |
| 3. Incident Board | [variant-3-incident-board.svg](variant-3-incident-board.svg) | Экран для разбора деградаций: health, текущие риски, действия и live servers. | Удобен, когда связь есть, но "тормозит" или часто переключается. |
| 4. Timeline Console | [variant-4-timeline-console.svg](variant-4-timeline-console.svg) | События и состояние во времени как главный объект экрана. | Лучше показывает причинно-следственную связь: проверка, деградация, failover, восстановление. |
| 5. Operator Console | [variant-5-operator-console.svg](variant-5-operator-console.svg) | Компактный трехколоночный dashboard для постоянного наблюдения. | Самый практичный вариант для текущей версии; реализован как `/dashboard-v5`. |

## Что уже реализовано

Вариант 5 сначала был реализован как второй тестовый dashboard, а затем стал основным экраном статуса:

- `/`
- `/status`
- `/dashboard-v5`
- alias `/v5`
- lazy fragments endpoint `/fragments/dashboard-v5`
- предыдущий dashboard сохранен как `/dashboard-classic`

## Идеи для дальнейшего UI

- Добавить явную строку "traffic path": client -> inbound -> active pool -> selected outbound.
- Разделить события на типы: subscription, health, quality, failover, DNS/assets.
- Сделать короткий "why degraded" блок: какой check сломался и какое действие supervisor уже сделал.
- На странице live servers добавить фильтры `extra/subscription`, `preferred region`, `transport`, `recent fail`.
- Для `/dashboard-v5` можно добавить компактный sparkline качества за последние 30-60 минут, если появится нормальная история метрик.
