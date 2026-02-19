# Архитектура: 1200 email-адресов для аутрича (multi-tenant)

## Цели

- Обслуживать ~1200 почтовых ящиков для отправки/получения аутрич-писем.
- Несколько клиентов и направлений (multi-tenant), изоляция репутации.
- Минимальная стоимость инфраструктуры при высокой отказоустойчивости.
- Управляемая ротация отправителей, прогрев, лимиты, мониторинг доставляемости.

## Предлагаемая архитектура (low-cost + HA)

### 1) Контур данных и конфигурации

- **PostgreSQL (managed)**
  - Таблицы: `tenants`, `campaigns`, `mailboxes`, `sending_policies`, `events` (delivered/bounced/complaint/open/reply), `provider_health`.
  - Причина: транзакционность, удобная аналитика, дешево на managed.
- **Object Storage (S3/Backblaze/R2)**
  - Шаблоны писем, вложения, логи (по ретеншн-политикам).

### 2) Очереди и исполнение

- **Очередь задач (Redis + RQ/Sidekiq-аналог на Python, либо managed SQS/PubSub)**
  - Типы задач: `send_email`, `warmup_send`, `imap_sync`, `bounce_process`, `webhook_ingest`, `health_check`.
- **Workers (контейнеры)**
  - Горизонтально масштабируются.
  - Stateless. Конфиг забирают из БД.

### 3) Отправка писем

- **SMTP-исполнитель**
  - Пул соединений на домен/провайдера.
  - Поддержка rate limiting на уровне mailbox + домен получателя.
- **Ротация**
  - На каждую кампанию задаётся пул `mailboxes` + политика: дневной лимит, лимит в минуту, «тихие часы», max подряд ошибок.
  - Алгоритм: weighted round-robin с учетом health-score ящика и домена получателя.
  - Авто-вывод ящика из ротации при росте hard bounce/blocks.
- **Deliverability контур**
  - На домены отправки: SPF/DKIM/DMARC, отдельные subdomain’ы, раздельные IP (если потребуется).

### 4) Получение ответов/бенсов

- **IMAP sync** (или webhooks, если провайдер поддерживает)
  - Периодический sync входящих для классификации reply/OOO/bounce.
- **Parser/Classifier**
  - Простейшие правила + возможность подключить LLM-классификацию (опционально) для intent (positive/negative/meeting/OOO).

### 5) Наблюдаемость и отказоустойчивость

- **Metrics**: Prometheus/Grafana (или Managed)
  - очереди, latency задач, send-rate, SMTP error codes, bounce-rate, spam/complaints.
- **Logs**: централизованные (Loki/ELK/Cloud logging)
- **Alerting**
  - SLO: % успешных отправок, рост 4xx/5xx по конкретному провайдеру, деградация домена.
- **Health-check сервис**
  - периодические SMTP/IMAP проверки ящиков.

## Как распределяется нагрузка

- Шедулер (cron/beat) раскладывает задачи в очередь по кампаниям.
- Workers тянут задачи; concurrency ограничивается:
  - per-mailbox limit
  - per-tenant global limit
  - per-recipient-domain throttling
- При пиках: добавляем workers (дешево, stateless).

## Риски и как закрывать

- **Провайдеры блокируют RCPT-проверку / greylisting**
  - Считать SMTP-handshake как best-effort; основной источник истины — bounce processing.
- **Репутационные риски (spam)**
  - Прогрев ящиков, постепенный ramp-up; отдельные домены; строгие лимиты; контент-валидация.
- **Мульти-тенант утечки**
  - Row-level security / строгая tenant-изоляция; отдельные домены отправки на крупных клиентов.
- **Сбой очереди/БД**
  - Managed PostgreSQL с бэкапами; Redis/SQS с persistence; idempotency keys для задач.

## Примерная стоимость (очень грубо)

- **Managed Postgres**: $20–80/мес (в зависимости от провайдера/размера).
- **Redis / очередь**: $10–50/мес (или SQS копейки за запросы).
- **2–6 workers (VM/containers)**: $30–200/мес (autoscale).
- **Monitoring/logging**: $0–100/мес (зависит от объёма логов).

Итого: примерно **$60–400/мес** за инфраструктуру, без учета стоимости доменов/почтовых ящиков/прокси.
