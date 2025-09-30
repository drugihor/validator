# Email Validator Pro

Мощный API для валидации email адресов с поддержкой различных методов проверки.

## Возможности

- ✅ SMTP валидация
- ✅ MX записи DNS
- ✅ IMAP проверка
- ✅ POP3 проверка  
- ✅ HTTP валидация
- ✅ Поддержка прокси
- ✅ Массовая валидация
- ✅ API ключи для аутентификации

## Методы валидации

1. **SMTP** - Проверка через SMTP протокол
2. **MX** - Проверка MX записей домена
3. **IMAP** - Проверка входа через IMAP
4. **POP3** - Проверка входа через POP3
5. **HTTP** - HTTP валидация для популярных сервисов

## Деплой на Render

### Шаг 1: Подготовка репозитория

1. Загрузи код в свой GitHub репозиторий
2. Убедись, что все файлы на месте:
   - `render.yaml`
   - `requirements.txt` 
   - `app.py`
   - `config.py`
   - `validator.py`

### Шаг 2: Создание сервиса на Render

1. Зайди на [render.com](https://render.com)
2. Нажми "New+" → "Web Service"
3. Подключи свой GitHub репозиторий
4. Render автоматически обнаружит `render.yaml`

### Шаг 3: Настройка переменных окружения

В настройках сервиса добавь:
- `API_KEY` - сгенерируй сильный API ключ

### Шаг 4: Деплой

Render автоматически:
- Установит зависимости из `requirements.txt`
- Запустит приложение через Gunicorn
- Выдаст URL для доступа к API

## Локальная разработка

### Установка зависимостей

```bash
pip install -r requirements.txt
```

### Настройка окружения

1. Скопируй `.env.example` в `.env`:
```bash
cp .env.example .env
```

2. Установи свой API ключ в `.env`:
```
API_KEY=your_secret_api_key_here
```

### Запуск

```bash
python app.py
```

API будет доступен на `http://localhost:5000`

## Использование API

### Проверка одного email

```bash
curl -X POST https://your-app.onrender.com/api/validate-single-email \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{
    "email": "test@gmail.com",
    "password": "password123",
    "method": "auto"
  }'
```

### Проверка нескольких email

```bash
curl -X POST https://your-app.onrender.com/api/validate-multiple-emails \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{
    "emails": [
      {"email": "test1@gmail.com", "password": "pass1"},
      {"email": "test2@yahoo.com", "password": "pass2"}
    ],
    "method": "auto"
  }'
```

## Параметры запроса

- `email` - email для проверки (обязательно)
- `password` - пароль для IMAP/POP3/SMTP проверки
- `method` - метод валидации: "auto", "smtp", "mx", "imap", "pop3", "http"
- `proxy` - настройки прокси (опционально)
- `delay_seconds` - задержка между попытками (по умолчанию 0.1)

## Ответ API

```json
{
  "email": "test@gmail.com",
  "is_valid": true,
  "status": "valid",
  "method_used": "smtp",
  "details": "SMTP RCPT TO success",
  "proxy_used": null
}
```

## Статусы валидации

- `valid` - email валиден  
- `invalid` - email не валиден
- `disposable` - одноразовый email
- `error` - ошибка при проверке

## Безопасность

- API защищен ключом в заголовке `X-API-Key`
- Не храни пароли в логах
- Используй HTTPS в продакшне