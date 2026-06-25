# Roadmap v2: pool-based proxy-xray gateway

Статус: техническое задание для последовательной доработки текущего решения.

Решение: текущий проект не переписываем с нуля и не выносим в новый репозиторий. Делаем v2 внутри существующего проекта, сохраняя рабочую модель домашнего LAN proxy gateway.

## 0. Текущий статус реализации

- `v1` branch должен указывать на последнюю стабильную текущую версию.
- `main` используется для последовательной реализации v2.
- Первый технический шаг v2: ввести модель `active_pool` / `standby_pool` без изменения runtime-поведения Xray.
- Второй технический шаг v2: запустить active и standby slots с маленькими Xray balancer pools.
- Публичные порты остаются стабильными: `1080`, `8123`, `10086`, `18080`.

Главное ограничение: функционал прокси обязателен. После всех доработок должны сохраниться стабильные пользовательские порты:

- `1080` - SOCKS proxy;
- `8123` - HTTP proxy;
- `10086` - LAN VLESS inbound;
- `18080` - status UI;
- split DNS и direct routing для RU ресурсов;
- загрузка подписки и `vless-extra.txt`;
- Telegram уведомления;
- Docker Compose запуск и SSH deploy.

## 1. Цель v2

Текущая цель проекта остается прежней: домашний LAN-шлюз на Xray, который принимает подключения от браузера, компьютера, телефона и Android TV, а наружу ходит через автоматически выбранные VLESS-серверы из подписки и личного списка.

v2 должна решить текущую архитектурную проблему: сейчас supervisor слишком часто сам принимает решения уровня dataplane. Он держит active и hot standby как отдельные Xray-процессы с одним кандидатом в каждом slot, а затем переключает локальные TCP-switch'и между slot'ами.

Это работает, но плохо масштабируется по логике:

- один active-сервер может деградировать, пока другой рабочий сервер уже есть в списке;
- переключение по throughput может занимать до 15 минут;
- Xray `observatory` и `balancer` используются не в полную силу;
- supervisor loop становится слишком большим и трудно предсказуемым;
- статус показывает много косвенных признаков, но не всегда ясно, почему выбран именно этот путь.

Цель v2: разделить обязанности.

- Xray должен быстро выбирать лучший outbound внутри небольшого пула.
- Python supervisor должен управлять пулом, состоянием, историей, карантином, UI, Telegram и deploy-операциями.

## 2. Нецели v2

v2 не должна превращать проект в новый продукт общего назначения.

Не делаем в рамках v2:

- переход на TUN/router-level gateway;
- замену Xray на sing-box, mihomo или другой движок;
- публичную web-admin панель;
- авторизацию в status UI;
- React/Vue/frontend build pipeline;
- массовую многопоточную проверку всех серверов подписки;
- хранение секретов в git;
- поддержку старых legacy-режимов, уже удаленных из проекта.

Эти направления можно исследовать отдельно, но v2 должна улучшить именно текущую Docker Compose LAN-модель.

## 3. Текущая архитектура, которую считаем baseline

Сейчас проект состоит из таких крупных частей:

- `run.sh` - entrypoint, готовит DNS/routing flags и запускает supervisor;
- `proxy_xray/main.py` - CLI flags supervisor;
- `proxy_xray/supervisor.py` - основной control loop;
- `proxy_xray/xray_config.py` - генерация Xray JSON;
- `proxy_xray/xray_process.py` - запуск Xray и curl-проверки;
- `proxy_xray/vless.py` - VLESS parsing, scoring, ranking, quarantine;
- `proxy_xray/subscription.py` - загрузка подписки и extra-ссылок;
- `proxy_xray/candidate_checker.py` - последовательная случайная проверка кандидатов;
- `proxy_xray/status.py` и `proxy_xray/status_server.py` - runtime status и UI;
- `dns-split-proxy.py` - split DNS relay;
- `scripts/deploy-server.sh` - SSH deploy.

Текущие сильные стороны:

- проект уже запускается через `docker compose up`;
- есть стабильные прокси-порты;
- есть LAN VLESS inbound для клиентов, которые не умеют HTTP/SOCKS;
- есть subscription refresh и fallback на cached state;
- есть приоритет личных extra-серверов;
- есть RU direct routing и split DNS;
- есть hot standby;
- есть статусная страница;
- есть Telegram уведомления;
- есть deploy на домашний сервер;
- есть smoke-test контейнер.

Текущие слабые стороны:

- active slot и standby slot фактически получают по одному кандидату;
- Xray balancer внутри slot'а почти не балансирует, потому что выбирать не из чего;
- результаты Xray observatory не читаются supervisor'ом;
- heavy throughput check используется как failover signal, поэтому реакция на тормоза может быть слишком медленной;
- нет явной state machine для failover;
- score построен на последних значениях, но нет полноценной истории качества;
- UI показывает много деталей, но не показывает путь принятия решения достаточно явно;
- диагностика проблем с частичной загрузкой картинок пока недостаточно доменная.

## 4. Целевая архитектура v2

Целевая модель:

```text
subscription + vless-extra
        |
        v
candidate loader
        |
        v
state + history + quarantine
        |
        v
pool selector
        |
        +--> active pool: 3-5 candidates
        |
        +--> standby pool: 2-3 candidates
        |
        v
two Xray slots
        |
        +--> active slot: Xray balancer/observatory over active pool
        |
        +--> standby slot: Xray balancer/observatory over standby pool
        |
        v
TCP switches keep public ports stable
        |
        v
SOCKS 1080 / HTTP 8123 / LAN VLESS 10086
```

Python supervisor остается control plane.

Xray становится dataplane selector внутри каждого slot'а.

Важная идея: мы не проверяем всю подписку параллельно. Мы даем Xray небольшой пул хороших кандидатов, а не сотни серверов. Это снижает риск упереться в лимит подписки и уменьшает шум.

## 5. Основные принципы реализации

1. Сначала сохранить работоспособность текущего режима.

   Любой этап v2 должен быть откатываемым. Если pool mode ломается, должна быть возможность вернуться к single-candidate mode или к предыдущему commit.

2. Не ломать публичные порты.

   Для клиентов в LAN ничего не должно измениться: браузер, V2RayTun и Android TV продолжают ходить в те же адреса и порты.

3. Не долбить подписку.

   Проверки кандидатов остаются последовательными. Xray observatory внутри active/standby pool допускается только для малого пула.

4. RU routing и split DNS - first-class feature.

   Любая новая генерация Xray config должна сохранять direct routing для RU доменов/IP и split DNS поведение.

5. UI остается простым.

   Без frontend framework. Обычный HTML/CSS из Python status server.

6. Секреты не попадают в репозиторий и diagnostic exports.

   Subscription URL, Telegram token, UUID и VLESS links должны маскироваться.

## 6. Функциональные требования v2

### 6.1. Proxy compatibility

Нужно сохранить:

- SOCKS proxy на `1080/tcp` и `1080/udp`;
- HTTP proxy на `8123/tcp`;
- LAN VLESS inbound на `10086/tcp`;
- status UI на `18080/tcp`;
- работу через Docker Compose;
- работу через SSH deploy на домашний сервер.

Критерий приемки:

- существующий браузер с SOCKS продолжает открывать сайты;
- HTTP proxy проверяется через `curl -x http://127.0.0.1:8123`;
- LAN VLESS проверяется smoke-контейнером;
- после переключения active slot клиенты не меняют настройки.

### 6.2. Active pool вместо одного active-кандидата

Нужно заменить active slot с одного кандидата на небольшой пул.

Предлагаемые параметры по умолчанию:

```text
active_pool_size = 3
active_pool_min_live = 2
active_pool_max_subscription = 4
active_pool_prefer_extra = true
```

Правила выбора:

- extra-серверы имеют повышенный приоритет;
- preferred regions: US/EU;
- RU/Russia subscription candidates отбрасываются как сейчас;
- non-preferred регионы допускаются только если не хватает preferred candidates;
- quarantined candidates не попадают в pool;
- активный пул должен быть отсортирован по score, но не состоять из дублей одного endpoint, если есть альтернатива;
- если есть недавно проверенные live candidates, они приоритетнее unchecked candidates.

Критерий приемки:

- `/json` показывает `active_pool` с 3-5 кандидатами;
- Xray config active slot содержит несколько `proxy-*` outbounds;
- traffic через `1080` идет через balancer active slot;
- при падении одного outbound внутри active pool Xray может выбрать другой без Python-level hot switch.

### 6.3. Standby pool вместо одного standby-кандидата

Hot standby должен также стать пулом.

Предлагаемые параметры по умолчанию:

```text
standby_pool_size = 3
standby_pool_min_live = 1
standby_pool_disjoint_from_active = true
```

Правила:

- standby pool не должен повторять active pool, если хватает кандидатов;
- standby pool также предпочитает extra/US/EU/live candidates;
- если live candidates мало, standby может включить unchecked preferred candidates;
- если standby pool деградирует, supervisor должен перестроить только standby slot, не трогая active slot.

Критерий приемки:

- `/json` показывает `standby_pool`;
- UI показывает hot standby pool и его выбранный outbound;
- standby Xray process постоянно запущен;
- standby проверяется в фоне;
- при failover public ports переключаются на standby slot;
- если active path полностью упал, а standby уже healthy, hot switch происходит после первого полного fail активного пути.

### 6.4. Native Xray observatory/balancer внутри каждого pool

Каждый slot должен запускать Xray config с несколькими outbounds и native balancer.

Требования:

- `observatory` включен для `proxy-extra-*` и `proxy-sub-*` outbounds внутри slot'а;
- `enableConcurrency` остается `false`, если это возможно и достаточно;
- `probeInterval` задается флагом;
- default strategy: `leastPing`;
- fallback outbound = лучший candidate по нашему score;
- routing rules сохраняют RU direct и split DNS;
- API endpoint Xray включен для чтения routing/observatory данных.

Критерий приемки:

- generated config active slot содержит pool candidates;
- generated config standby slot содержит pool candidates;
- Xray стартует с этими config;
- smoke-тесты proxy/HTTP/VLESS проходят;
- Xray observatory не начинает массово проверять всю подписку.

### 6.5. Чтение результатов Xray observatory

Supervisor должен читать, что Xray думает о каждом outbound.

Цель:

- видеть реальное состояние outbounds внутри active/standby pool;
- понимать, какой outbound выбран Xray balancer;
- обновлять candidate history на основании observatory;
- показывать данные в UI.

Реализация:

- добавить `proxy_xray/xray_api.py`;
- исследовать доступный API Xray для observatory/routing;
- реализовать безопасный client с timeout;
- при недоступности API не ломать supervisor, а показывать `unknown`;
- сохранять observatory snapshot в status state.

Критерий приемки:

- `/json` содержит `active_observatory` и `standby_observatory`;
- UI показывает состояние outbounds внутри pool;
- видно, какой outbound выбран или считается лучшим;
- если API недоступен, health-checkи продолжают работать.

### 6.6. Быстрая диагностика деградации

Текущая модель:

- liveness/latency check раз в 60 секунд;
- throughput check раз в 300 секунд;
- throughput failover после 3 плохих проверок, то есть до 15 минут.

Для v2 это слишком медленно.

Нужно разделить проверки:

1. Liveness check.

   Быстрый короткий запрос, который отвечает на вопрос: "маршрут вообще живой?"

   Предложение:

   ```text
   liveness_interval = 20s
   liveness_failures_for_failover = 2
   liveness_timeout = 5s
   ```

2. Latency check.

   Проверка задержки короткого запроса.

   Предложение:

   ```text
   latency_degrade_threshold = 3s
   latency_degrade_checks = 2 или 3
   ```

3. Small download check.

   Небольшая загрузка 256-512 KB, которая ловит частичную деградацию лучше, чем `generate_204`, но не так дорого стоит, как 2 MB speedtest.

   Предложение:

   ```text
   quality_download_interval = 60s
   quality_download_size = 256KB или 512KB
   quality_min_kbps = 1000
   quality_degrade_checks = 2
   ```

4. Heavy throughput check.

   Оставить как метрику качества и score, но не как главный быстрый failover trigger.

   Предложение:

   ```text
   throughput_check_interval = 300s или 600s
   throughput_degrade_checks = 2 или 3
   ```

Критерий приемки:

- полный обрыв распознается примерно за 40-60 секунд;
- сильная деградация распознается примерно за 1-3 минуты;
- тяжелый speedtest не запускается слишком часто;
- UI показывает отдельно liveness, latency, quality download и throughput;
- Telegram notification содержит причину: liveness, latency, quality, throughput.

### 6.7. Domain probes для реальных проблем

Нужно добавить диагностические проверки не только generic URL, но и доменных сценариев.

Причина: проблема с неполной загрузкой картинок может не проявляться на `generate_204`.

Минимальный набор:

- `https://www.gstatic.com/generate_204` - базовый HTTPS liveness;
- Cloudflare small download - качество канала;
- один image/CDN probe для проблемного сайта, например Pikabu image host, если URL стабилен;
- direct RU probe через direct route, например `https://yandex.ru` или другой легкий RU endpoint;
- global DNS probe и RU DNS probe.

Требования:

- domain probes не должны ломать работу при временной недоступности конкретного сайта;
- domain probes должны быть диагностикой, а не единственным failover trigger;
- список probe URL должен настраиваться.

Критерий приемки:

- `/diagnostics` или `/json` показывает результаты domain probes;
- при проблемах с картинками видно, какой probe деградировал;
- probe errors логируются структурированно.

### 6.8. Явная state machine для failover

Нужно заменить неявную логику в большом loop на явную модель состояний.

Предлагаемые состояния:

- `STARTING`;
- `HEALTHY`;
- `DEGRADED`;
- `FAILING`;
- `SWITCHING_TO_STANDBY`;
- `REBUILDING_STANDBY`;
- `COOLDOWN`;
- `NO_STANDBY`;
- `RECOVERED`.

События:

- `liveness_failed`;
- `liveness_recovered`;
- `latency_degraded`;
- `quality_degraded`;
- `throughput_degraded`;
- `active_pool_changed`;
- `standby_pool_ready`;
- `standby_pool_unhealthy`;
- `subscription_refreshed`;
- `assets_refreshed`;
- `manual_refresh`;
- `manual_pin`;
- `cooldown_expired`.

Критерий приемки:

- в коде есть отдельный модуль, например `proxy_xray/failover.py`;
- state transitions покрыты unit-тестами;
- `/json` показывает `failover_state`;
- UI показывает текущее состояние понятным текстом;
- логи пишут переходы состояния, а не только отдельные события.

### 6.9. Карантин и история качества v2

Текущий quarantine полезен, но состояние кандидата нужно сделать богаче.

Для каждого candidate нужно хранить:

- last OK;
- last FAIL;
- last latency;
- last small download kbps;
- last heavy throughput kbps;
- OK count за 24 часа;
- FAIL count за 24 часа;
- degraded count за 24 часа;
- сколько раз был выбран в active pool;
- сколько раз был выбран Xray внутри active pool, если это можно прочитать;
- сколько успешных failover было на этот candidate/pool;
- quarantine reason;
- quarantine until.

State file должен получить versioning:

```json
{
  "schema_version": 2,
  "candidates": [],
  "history": {},
  "last_active_pool": [],
  "last_standby_pool": []
}
```

Требования:

- v1 `state.json` должен мигрировать автоматически;
- если migration не удалась, сервис должен стартовать с candidates из subscription/extra, но сохранить старый state backup;
- state write должен быть atomic.

Критерий приемки:

- `state.example.json` обновлен;
- migration покрыта тестом;
- старый state не теряется;
- UI показывает историю качества без перегруза.

### 6.10. Scoring v2

Score должен стать не просто "приятной цифрой", а объяснимой моделью выбора pool.

Сигналы:

- source: extra/subscription;
- preferred region;
- non-preferred region penalty;
- transport preference;
- recent OK;
- recent fail;
- latency;
- small download;
- heavy throughput;
- quarantine;
- historical stability;
- endpoint diversity;
- manual pin/boost, если добавим.

Требования:

- score reasons остаются в `/json` и UI;
- score не должен бесконечно держать старый сервер наверху только из-за priority boost;
- recent fail должен быстро выталкивать candidate из pool;
- extra-серверы имеют преимущество, но не абсолютное.

Критерий приемки:

- sorted live servers понятны оператору;
- видно, почему candidate в active pool;
- видно, почему candidate не попал в pool;
- score tests покрывают типовые ситуации.

### 6.11. Status UI v2

UI должен отвечать на вопросы:

- работает ли gateway;
- какой active pool сейчас используется;
- какой outbound внутри active pool выбран Xray;
- какой standby pool готов;
- почему было последнее переключение;
- сколько осталось cooldown/quarantine;
- что с подпиской;
- что с DNS/RU direct;
- что с geo assets;
- какой следующий candidate check;
- какие последние важные события.

Новые блоки:

- `Active pool`;
- `Standby pool`;
- `Xray selected outbound`;
- `Failover state`;
- `Quality checks`;
- `Pool decision reasons`;
- `Domain diagnostics`;
- `Recent important events`.

Требования:

- без React/Vue;
- без авторизации;
- mobile-friendly enough;
- списки больших кандидатов остаются отдельными страницами `/servers/live` и `/servers/all`;
- секреты не выводятся.

Критерий приемки:

- на главной странице не нужно читать logs, чтобы понять состояние;
- active/standby pool видны сразу;
- причина последнего switch видна в UI;
- score и reasons остались в таблицах.

### 6.12. Diagnostics endpoint

Добавить endpoint:

```text
/diagnostics
```

Он должен показывать:

- текущий active slot;
- текущий active pool;
- текущий Xray selected outbound;
- hot standby pool;
- liveness/latency/quality/throughput checks;
- split DNS RU/global checks;
- RU direct route smoke;
- geoip/geosite состояние;
- subscription state;
- last switch;
- quarantine summary;
- последние ошибки Xray API/observatory.

Опционально добавить:

```text
/diagnostics/bundle
```

Diagnostic bundle должен маскировать секреты.

Критерий приемки:

- можно открыть `/diagnostics` в LAN и понять, что именно сломалось;
- smoke-test контейнер может переиспользовать часть diagnostics logic;
- bundle безопасен для отправки без ручной чистки секретов.

### 6.13. Deploy и rollback

v2 повысит риск ошибок, поэтому deploy нужно усилить.

Требования:

- named deploy profile, например `home`;
- backup server files перед deploy;
- rollback на предыдущую версию;
- smoke-check после deploy;
- сохранение server-side `state.json`, `.env`, `vless-extra.txt`, `assets/`;
- печать итогового URL UI и proxy ports.

Предлагаемый UX:

```sh
scripts/deploy-server.sh home
scripts/deploy-server.sh home --rollback latest
scripts/deploy-server.sh home --smoke
```

Критерий приемки:

- не нужно вручную копировать через shell/Far;
- неуспешный deploy можно откатить;
- runtime state на сервере не затирается случайно.

## 7. Последовательный план реализации

### Этап 0. Зафиксировать baseline и безопасность

Цель: перед v2 убедиться, что текущая рабочая версия воспроизводима.

Работы:

- проверить, что текущий UI layout commit сохранен;
- убедиться, что `.env`, `state.json`, `vless-extra.txt`, `assets/` не отслеживаются git;
- добавить или обновить smoke checklist;
- сохранить текущую рабочую версию в git;
- сделать backup server-side runtime files.

Критерий готовности:

- есть commit текущего стабильного состояния;
- есть понятная команда deploy;
- есть понятная команда rollback или хотя бы manual backup.

### Этап 1. Ввести модели pool без изменения runtime behavior

Цель: добавить структуры данных, но пока не менять запуск Xray.

Работы:

- создать модуль `proxy_xray/pool.py`;
- описать `PoolSelection`;
- добавить функции выбора active/standby pool;
- пока active pool size = 1 и standby pool size = 1 по умолчанию;
- добавить unit-тесты на selection rules;
- добавить поля pool в `/json`, но без смены механики.

Почему так:

- можно проверить новую модель без риска сломать прокси;
- проще покрыть selection logic тестами.

Критерий готовности:

- текущий Docker Compose работает как раньше;
- `/json` уже показывает pool abstraction;
- smoke-тесты проходят.

### Этап 2. Active pool в одном slot

Статус: реализовано в первой v2-итерации. Active slot запускается с несколькими outbounds, startup preflight проверяет fallback до подключения публичных портов.

Дополнение: active pool резервирует до одного live extra-кандидата, если такой есть, чтобы не тратить все места pool на подписку.

Цель: active slot получает несколько candidates, но standby пока можно оставить простым.

Работы:

- изменить `start_slot`, чтобы он принимал список candidates;
- изменить slot state: `candidate` -> `candidates`, `selected_candidate`;
- использовать `make_native_balancer_config(pool_candidates, ...)`;
- добавить flags:

```text
--active-pool-size
--active-pool-min-live
--pool-prefer-extra
```

- сохранить TCP switches как есть;
- в UI показать active pool.

Почему так:

- главный выигрыш появится уже здесь: Xray сможет выбирать внутри active pool без Python switch.

Критерий готовности:

- active Xray config содержит несколько outbounds;
- SOCKS/HTTP/LAN VLESS работают;
- RU direct routing сохранился;
- Xray process не рестартится бесконечно из-за неустойчивого fingerprint.

### Этап 3. Standby pool

Статус: реализовано в первой v2-итерации. Standby slot запускается отдельным Xray process с собственным небольшим pool и быстрым promotion при полном отказе active path.

Дополнение: standby pool также резервирует до одного live extra-кандидата; если есть другой live extra URI, он предпочтительнее, но если live extra только один, standby может переиспользовать active extra URI.

Цель: standby slot тоже получает несколько candidates.

Работы:

- добавить `--standby-pool-size`;
- сделать active/standby pools disjoint where possible;
- перестраивать standby pool при деградации standby;
- UI показывает standby pool;
- failover переключает TCP switches на standby slot, как сейчас.

Почему так:

- failover будет переключать не на один сервер, а на подготовленный запасной pool.

Критерий готовности:

- active и standby slot одновременно работают с pool configs;
- standby health отображается;
- при failover новый active имеет несколько outbounds.

### Этап 4. Xray API и observatory status

Статус: реализовано в практическом объеме текущего Xray API. Supervisor читает `xray api bi` для active и standby slot, `/json` содержит `active_observatory`/`standby_observatory`, UI показывает selected outbound из Xray API. Текущий API отдает selected tag без latency, поэтому candidate history обновляется по selected outbound только после успешного slot health/quality check.

Цель: supervisor начинает читать состояние, которое видит Xray.

Работы:

- добавить `proxy_xray/xray_api.py`;
- включить нужные Xray API services в config;
- читать observatory/routing state по active и standby api ports;
- обновлять candidate stats по observatory selected outbound после успешных slot checks;
- отображать Xray selected/best outbound в UI.

Почему так:

- мы перестаем гадать по косвенным проверкам;
- становится понятно, почему Xray выбрал конкретный outbound.

Критерий готовности:

- `/json` содержит observatory snapshot;
- UI показывает selected outbound;
- при недоступном API supervisor не падает.

### Этап 5. Быстрые quality checks

Статус: реализовано. Compose использует liveness interval 20s / 2 failures, latency threshold 3s, small quality download 512 KB раз в 60s с failover после 2 медленных проверок. Heavy throughput оставлен как quality metric по умолчанию.

Цель: сократить время реакции на деградацию.

Работы:

- разделить current health на liveness, latency, small download, throughput;
- heavy throughput перевести в score/quality signal;
- добавить flags для новых интервалов;
- обновить health indicators и UI;
- обновить Telegram message.

Почему так:

- "интернет как модем" должен распознаваться быстрее 15 минут;
- heavy speedtest не должен быть главным быстрым триггером.

Критерий готовности:

- обрыв ловится примерно за 40-60 секунд;
- сильная деградация ловится за 1-3 минуты;
- тяжелая проверка не запускается слишком часто.

### Этап 6. Failover state machine

Статус: частично реализовано. Decision layer вынесен в `proxy_xray/failover.py`: причины failover, full-failure bypass cooldown и cooldown suppression теперь считаются чистыми функциями и покрыты unit-тестами. Supervisor публикует `failover_state` в `/json` и UI. Реальное исполнение switch/rebuild slot пока остается в `supervisor.py`.

Цель: сделать переключения предсказуемыми и тестируемыми.

Работы:

- вынести failover transitions в `proxy_xray/failover.py`;
- описать состояния и события;
- покрыть transitions тестами;
- логировать transitions;
- показать `failover_state` в UI.

Почему так:

- текущий loop уже сложный;
- дальше без state machine будет трудно безопасно менять поведение.

Критерий готовности:

- по logs понятно, почему произошло или не произошло переключение;
- cooldown/quarantine поведение покрыто тестами.

### Этап 7. State schema v2 и история качества

Статус: реализовано. `state.json` теперь имеет `schema_version: 2`, rolling quality stats, bounded recent checks, atomic save и graceful recovery при поврежденном JSON.

Цель: накапливать качество серверов, а не только последнее значение.

Работы:

- добавить `schema_version`;
- добавить migration из текущего state;
- добавить rolling stats;
- писать state atomic;
- обновить `state.example.json`;
- обновить scoring.

Почему так:

- стабильный сервер и случайно оживший сервер должны различаться;
- score должен меньше прыгать от одной проверки.

Критерий готовности:

- старый state читается;
- новый state сохраняет history;
- score reasons объясняют исторические бонусы/штрафы.

### Этап 8. Diagnostics и domain probes

Цель: быстрее разбирать проблемы вроде неполной загрузки картинок.

Работы:

- добавить `/diagnostics`;
- добавить configurable domain probes;
- добавить masked diagnostic bundle;
- включить checks в smoke-test container.

Почему так:

- generic health URL не покрывает реальные CDN/image проблемы.

Критерий готовности:

- можно открыть `/diagnostics` и увидеть, где именно проблема;
- diagnostic bundle не содержит секретов.

### Этап 9. Deploy profiles и rollback

Цель: сделать обновления безопасными.

Работы:

- named profile для домашнего сервера;
- backup перед deploy;
- rollback;
- smoke после deploy;
- понятный итог deploy.

Почему так:

- v2 будет менять control plane, нужен быстрый откат.

Критерий готовности:

- deploy выполняется одной командой;
- rollback выполняется одной командой;
- server runtime files не затираются.

### Этап 10. Удаление старой single-candidate логики

Цель: после стабилизации v2 убрать лишние ветки.

Работы:

- удалить устаревшие paths, которые поддерживали только single-candidate slots;
- обновить README;
- обновить project-changes;
- обновить roadmap/completed-roadmap;
- привести tests к новой архитектуре.

Почему так:

- не нужно держать две реализации бесконечно;
- проект должен оставаться простым для одного поддерживаемого runtime mode.

Критерий готовности:

- single supported mode = pool-based subscription supervisor;
- README описывает реальное поведение;
- smoke-тесты проходят.

## 8. Предлагаемые новые CLI flags

Предварительный список:

```text
--active-pool-size 3
--active-pool-min-live 2
--standby-pool-size 3
--standby-pool-min-live 1
--pool-disjoint-active-standby
--pool-prefer-extra
--pool-preferred-regions us,eu

--liveness-check-interval 20
--liveness-max-failures 2
--liveness-timeout 5

--quality-check-interval 60
--quality-url https://speed.cloudflare.com/__down?bytes=512000
--quality-min-kbps 1000
--quality-degrade-checks 2

--domain-probe-url <url>
--domain-probe-timeout 8

--failover-profile balanced
```

Важно: старые flags можно сохранить как aliases на время migration, но README должен описывать только актуальные flags v2.

## 9. Тестирование

Минимальные проверки после каждого этапа:

```sh
bash -n run.sh scripts/deploy-server.sh
python3 -m py_compile subscription-supervisor.py dns-split-proxy.py proxy_xray/*.py scripts/render-demo-status.py
docker compose config
```

После runtime-изменений:

```sh
docker compose build proxy-xray
docker compose up -d --force-recreate proxy-xray
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm proxy-client-test
```

Новые unit-тесты желательно добавить для:

- pool selection;
- score calculation;
- quarantine;
- state migration;
- failover transitions;
- diagnostics sanitization.

Smoke-тесты должны проверять:

- status UI;
- `/json`;
- SOCKS proxy;
- HTTP proxy;
- LAN VLESS;
- split DNS;
- RU direct routing;
- geo assets;
- active pool visible;
- standby pool visible;
- no secrets in diagnostic output.

## 10. Риски и как их снижать

### Риск: подписка ограничивает число одновременных VLESS-соединений

Митигируем:

- active pool 3-5;
- standby pool 2-3;
- candidate checker остается последовательным;
- Xray observatory не получает всю подписку сразу;
- no mass parallel checks.

### Риск: Xray observatory API окажется неудобным или нестабильным

Митигируем:

- сначала сделать API integration read-only;
- при ошибке API показывать `unknown`;
- оставить наши health checks как fallback;
- не завязывать failover только на observatory.

### Риск: pool mode усложнит отладку

Митигируем:

- UI показывает active pool, standby pool, selected outbound, score reasons;
- logs пишут pool decision;
- `/diagnostics` показывает machine-readable картину.

### Риск: частые переключения ухудшат пользовательский опыт

Митигируем:

- cooldown остается;
- state machine явно разделяет failure и degradation;
- failover profiles: conservative/balanced/aggressive;
- full failure может обходить cooldown, degradation - нет.

### Риск: RU direct routing сломается при новой генерации config

Митигируем:

- smoke-test direct rules;
- generated config проверять в тестах;
- split DNS не трогать без отдельной причины.

### Риск: state migration повредит накопленную историю

Митигируем:

- backup state перед migration;
- atomic write;
- migration tests;
- возможность стартовать с cached subscription/extra даже при поврежденной history.

## 11. Критерии завершения v2

v2 можно считать готовой, когда:

- текущие proxy-порты сохранены;
- active slot работает с pool из нескольких кандидатов;
- standby slot работает с pool из нескольких кандидатов;
- Xray balancer выбирает outbound внутри pool;
- supervisor читает и показывает observatory данные или корректно показывает их недоступность;
- failover state machine покрыта тестами;
- сильная деградация распознается быстрее текущих 15 минут;
- state хранит историю качества;
- UI объясняет, почему выбран текущий путь;
- smoke-тесты проходят;
- deploy и rollback понятны;
- README описывает v2 как единственный поддерживаемый runtime mode.

## 12. Предлагаемый порядок первых задач

Первый практический batch:

1. Закоммитить текущее стабильное состояние.
2. Добавить `proxy_xray/pool.py` без изменения runtime behavior.
3. Добавить unit-тесты pool selection.
4. Вывести `active_pool` и `standby_pool` в `/json`.
5. Показать pool summary в UI.
6. Только после этого менять `start_slot` на pool candidates.

Почему такой порядок:

- минимальный риск сломать домашний proxy;
- можно увидеть будущую модель в UI до смены dataplane;
- легче сравнить v1 и v2 поведение на одном и том же `state.json`.
