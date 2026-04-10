# 🔌 Настройка SSH подключения к серверу через Kiro

## Шаг 1: Установка расширения Remote-SSH

1. Нажмите `Ctrl+Shift+X` (открыть Extensions)
2. В поиске введите: **Remote - SSH**
3. Найдите расширение от **Microsoft** 
4. Нажмите **Install**

## Шаг 2: Создание SSH конфигурации

### Вариант A: Через PowerShell (Рекомендуется)

Откройте терминал в Kiro (Ctrl+`) и выполните:

```powershell
# Создать папку .ssh если её нет
mkdir -Force $env:USERPROFILE\.ssh

# Создать конфигурационный файл
@"
Host trading-server
    HostName YOUR_SERVER_IP
    User vladimirr
    Port 22
"@ | Out-File -FilePath "$env:USERPROFILE\.ssh\config" -Encoding UTF8
```

**⚠️ ВАЖНО:** Замените `YOUR_SERVER_IP` на реальный IP адрес вашего сервера!

Например, если IP сервера `192.168.1.100`, выполните:

```powershell
@"
Host trading-server
    HostName 192.168.1.100
    User vladimirr
    Port 22
"@ | Out-File -FilePath "$env:USERPROFILE\.ssh\config" -Encoding UTF8
```

### Вариант B: Вручную через Блокнот

1. Откройте Блокнот (notepad.exe)
2. Вставьте этот текст:

```
Host trading-server
    HostName YOUR_SERVER_IP
    User vladimirr
    Port 22
```

3. Замените `YOUR_SERVER_IP` на реальный IP
4. Сохраните файл как: `C:\Users\slepk\.ssh\config` (без расширения!)
5. В "Тип файла" выберите "Все файлы (*.*)"

## Шаг 3: Подключение к серверу

1. В Kiro нажмите `F1` или `Ctrl+Shift+P`
2. Введите: `Remote-SSH: Connect to Host...`
3. Выберите `trading-server` из списка
4. Введите пароль когда попросит
5. Дождитесь подключения (может занять 10-30 секунд)

## Шаг 4: Открытие папки проекта на сервере

После подключения:

1. Нажмите `File` → `Open Folder...`
2. Введите путь: `/home/vladimirr/bybit-catboost-bot-v2`
3. Нажмите `OK`
4. Введите пароль если попросит

Теперь вы работаете с файлами прямо на сервере! 🎉

## Шаг 5: Загрузка файлов на сервер

Если папки `bybit-catboost-bot-v2` еще нет на сервере:

1. Подключитесь к серверу через Remote-SSH
2. Откройте терминал (Ctrl+`)
3. Создайте папку:
   ```bash
   mkdir -p ~/bybit-catboost-bot-v2
   cd ~/bybit-catboost-bot-v2
   ```

4. Теперь скопируйте файлы с Windows на сервер:
   - В Kiro откройте локальную папку с ботом
   - Выделите все файлы
   - Правой кнопкой → Copy
   - Переключитесь на Remote-SSH подключение
   - Откройте папку `/home/vladimirr/bybit-catboost-bot-v2`
   - Правой кнопкой → Paste

## Шаг 6: Установка зависимостей на сервере

В терминале Kiro (подключенном к серверу):

```bash
# Обновить систему
sudo apt update

# Установить Python 3.12
sudo apt install -y python3.12 python3.12-venv python3-pip

# Перейти в папку проекта
cd ~/bybit-catboost-bot-v2

# Создать виртуальное окружение
python3.12 -m venv venv

# Активировать
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt

# Создать директории
mkdir -p logs models/pairs catboost_info
```

## Шаг 7: Запуск бота

```bash
# Убедитесь что venv активирован
source venv/bin/activate

# Запустите бота
python3.12 bybit_async_bot.py
```

Для запуска в фоне используйте screen:

```bash
# Создать screen сессию
screen -S trading-bot

# Активировать venv
source venv/bin/activate

# Запустить бота
python3.12 bybit_async_bot.py

# Отключиться: Ctrl+A затем D
# Вернуться: screen -r trading-bot
```

## 🔑 Настройка SSH ключа (опционально)

Чтобы не вводить пароль каждый раз:

### На Windows (PowerShell):

```powershell
# Создать SSH ключ
ssh-keygen -t rsa -b 4096 -f "$env:USERPROFILE\.ssh\id_rsa"

# Скопировать ключ на сервер
type $env:USERPROFILE\.ssh\id_rsa.pub | ssh vladimirr@YOUR_SERVER_IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

После этого подключение будет автоматическим без пароля!

## 📋 Полезные команды

### Управление SSH подключением:

- `F1` → `Remote-SSH: Connect to Host` - подключиться
- `F1` → `Remote-SSH: Close Remote Connection` - отключиться
- `F1` → `Remote-SSH: Show Log` - показать логи подключения

### Работа с файлами:

- `Ctrl+Shift+E` - Explorer (файловый менеджер)
- `Ctrl+P` - быстрый поиск файлов
- `Ctrl+Shift+F` - поиск по содержимому

### Терминал:

- `` Ctrl+` `` - открыть/закрыть терминал
- `Ctrl+Shift+5` - разделить терминал
- `Ctrl+Shift+C` - копировать из терминала
- `Ctrl+Shift+V` - вставить в терминал

## ❓ Решение проблем

### Проблема: "Could not establish connection"

1. Проверьте IP адрес сервера
2. Проверьте что SSH сервер запущен: `sudo systemctl status ssh`
3. Проверьте firewall: `sudo ufw status`
4. Попробуйте подключиться через обычный терминал: `ssh vladimirr@YOUR_SERVER_IP`

### Проблема: "Permission denied"

1. Проверьте имя пользователя (vladimirr)
2. Проверьте пароль
3. Проверьте права на сервере: `ls -la ~/.ssh/`

### Проблема: "Connection timeout"

1. Проверьте интернет соединение
2. Проверьте что сервер доступен: `ping YOUR_SERVER_IP`
3. Проверьте порт SSH (обычно 22)

## ✅ Готово!

После настройки вы сможете:
- ✅ Редактировать файлы на сервере как локальные
- ✅ Запускать команды в терминале на сервере
- ✅ Отлаживать код удаленно
- ✅ Синхронизировать файлы автоматически

Удачи с настройкой! 🚀
