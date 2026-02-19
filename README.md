# Polza Outreach Engine — тестовое

 Все команды ниже предполагают, что текущая директория — папка `polza_outreach_engine_test`.
 Если запускаете из другой директории, указывайте полные пути к файлам.

## 1) Проверка email-доменов (MX + SMTP handshake)

### Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

### Подготовка входных данных

Создайте файл `emails.txt` (1 email на строку).

### Запуск

```bash
python3 email_domain_check.py --input emails.txt
```

Опционально:

```bash
python3 email_domain_check.py --input emails.txt --sleep 0.2
python3 email_domain_check.py --input emails.txt --no-smtp
```

Вывод (по строке на email):

- `домен отсутствует`
- `MX-записи отсутствуют или некорректны`
- `домен валиден`

Дополнительно печатается `smtp=accepted|rejected|unknown` как best-effort результат RCPT-проверки.

## 2) Мини-интеграция с Telegram (бот -> приватный чат)

### Требования

- Создайте бота через `@BotFather`, получите токен.
- Добавьте бота в нужный приватный чат/группу.
- Узнайте `chat_id`.

### Запуск

Вариант 1 (через env):

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python3 telegram_send_text.py --file message.txt
```

Вариант 2 (флагами):

```bash
python3 telegram_send_text.py --file message.txt --bot-token "..." --chat-id "..."
```

### Тестовый режим (без отправки)

```bash
python3 telegram_send_text.py --file message.txt --bot-token "..." --chat-id "..." --dry-run
```

### Как быстро получить chat_id (проверка работоспособности бота)

1) Напишите любое сообщение боту (в личку) или в чат, где он есть.
2) Вызовите `getUpdates` (в браузере/через curl) и найдите `chat.id`:

`https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`

## 3) Архитектура + блиц

См. `ARCHITECTURE_AND_AI_STACK.md` (архитектура) и `AI_STACK.md` (блиц).
