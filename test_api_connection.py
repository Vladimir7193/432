"""
Тест подключения к Bybit API
Проверяет API ключи и получение баланса
"""
import os
import sys
from pybit.unified_trading import HTTP
import config as cfg

print("=" * 60)
print("  ТЕСТ ПОДКЛЮЧЕНИЯ К BYBIT API")
print("=" * 60)
print()

# Получить ключи
api_key = os.getenv("BYBIT_API_KEY", cfg.API_KEY)
api_secret = os.getenv("BYBIT_API_SECRET", cfg.API_SECRET)

print(f"API Key: {api_key[:10]}...{api_key[-4:]}")
print(f"API Secret: {'*' * 20}")
print(f"Testnet: {cfg.TESTNET}")
print()

# Создать сессию
try:
    session = HTTP(testnet=cfg.TESTNET, api_key=api_key, api_secret=api_secret)
    print("✅ Сессия создана")
except Exception as e:
    print(f"❌ Ошибка создания сессии: {e}")
    sys.exit(1)

print()
print("-" * 60)
print("ТЕСТ 1: Получение информации об аккаунте")
print("-" * 60)

try:
    resp = session.get_wallet_balance(accountType="UNIFIED")
    print("✅ Запрос выполнен успешно")
    print()
    print("Ответ API:")
    print(f"  retCode: {resp.get('retCode')}")
    print(f"  retMsg: {resp.get('retMsg')}")
    print()
    
    if resp.get("retCode") == 0:
        result = resp.get("result", {})
        account_list = result.get("list", [])
        
        if not account_list:
            print("❌ Аккаунт не найден!")
            print("   Проверь что используешь Unified Trading Account")
            sys.exit(1)
        
        account = account_list[0]
        coins = account.get("coin", [])
        
        print(f"Account Type: {account.get('accountType')}")
        print(f"Total Equity: {account.get('totalEquity')} USD")
        print(f"Total Wallet Balance: {account.get('totalWalletBalance')} USD")
        print(f"Total Available Balance: {account.get('totalAvailableBalance')} USD")
        print()
        
        # Найти USDT
        usdt_found = False
        for coin in coins:
            if coin.get("coin") == "USDT":
                usdt_found = True
                print("USDT баланс:")
                print(f"  Wallet Balance: {coin.get('walletBalance')}")
                print(f"  Available: {coin.get('availableToWithdraw')}")
                print(f"  Equity: {coin.get('equity')}")
                print(f"  Unrealized PnL: {coin.get('unrealisedPnl')}")
                
                available_raw = coin.get('availableToWithdraw', '')
                # Bybit Unified sometimes returns '' for availableToWithdraw
                # Fall back to totalAvailableBalance from account level
                if available_raw == '':
                    available = float(account.get('totalAvailableBalance') or 0)
                    print(f"  Available (from account): {available}")
                else:
                    available = float(available_raw)
                    print(f"  Available: {available}")
                
                if available > 0:
                    print()
                    print(f"✅ Баланс получен: ${available:.4f} USDT")
                else:
                    print()
                    print("⚠️  Доступный баланс = 0")
                    print("   Пополни счёт на Bybit")
                break
        
        if not usdt_found:
            print("❌ USDT не найден в аккаунте")
            print("   Пополни счёт USDT на Bybit")
    else:
        print(f"❌ Ошибка API: {resp.get('retMsg')}")
        print()
        print("Возможные причины:")
        print("  1. Неправильные API ключи")
        print("  2. Недостаточные права (нужно: Contract Trade → Read + Write)")
        print("  3. IP не в whitelist")
        print("  4. Ключи от testnet, а используется mainnet (или наоборот)")

except Exception as e:
    print(f"❌ Ошибка: {e}")
    print()
    print("Возможные причины:")
    print("  1. Неправильные API ключи")
    print("  2. Нет интернет соединения")
    print("  3. Bybit API недоступен")
    import traceback
    traceback.print_exc()

print()
print("-" * 60)
print("ТЕСТ 2: Получение позиций")
print("-" * 60)

try:
    resp = session.get_positions(category="linear", settleCoin="USDT")
    print("✅ Запрос выполнен успешно")
    
    if resp.get("retCode") == 0:
        positions = resp.get("result", {}).get("list", [])
        open_pos = [p for p in positions if float(p.get("size", 0)) > 0]
        
        if open_pos:
            print(f"Открытых позиций: {len(open_pos)}")
            for p in open_pos:
                print(f"  {p['symbol']:12} | {p['side']:4} | Size: {p['size']}")
        else:
            print("✅ Нет открытых позиций")
    else:
        print(f"❌ Ошибка: {resp.get('retMsg')}")

except Exception as e:
    print(f"❌ Ошибка: {e}")

print()
print("=" * 60)
print("  ТЕСТ ЗАВЕРШЁН")
print("=" * 60)
print()

input("Нажми Enter для выхода...")
