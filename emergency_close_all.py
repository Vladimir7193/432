"""
=============================================================
emergency_close_all.py — Экстренное закрытие всех позиций
=============================================================
Используй этот скрипт для быстрого закрытия всех открытых
позиций на Bybit в случае необходимости.

Запуск: python emergency_close_all.py
=============================================================
"""
import os
import sys
from pybit.unified_trading import HTTP
import config as cfg

def main():
    print("=" * 60)
    print("  ЭКСТРЕННОЕ ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ")
    print("=" * 60)
    print()
    
    # Получить ключи
    api_key = os.getenv("BYBIT_API_KEY", cfg.API_KEY)
    api_secret = os.getenv("BYBIT_API_SECRET", cfg.API_SECRET)
    
    if not api_key or not api_secret:
        print("❌ API ключи не найдены!")
        print("   Настрой их в config.py или переменных окружения")
        return
    
    # Подключение
    session = HTTP(testnet=False, api_key=api_key, api_secret=api_secret)
    
    try:
        # Получить все позиции
        resp = session.get_positions(category="linear", settleCoin="USDT")
        positions = resp["result"]["list"]
        
        open_positions = [p for p in positions if float(p.get("size", 0)) > 0]
        
        if not open_positions:
            print("✅ Нет открытых позиций")
            return
        
        print(f"Найдено открытых позиций: {len(open_positions)}")
        print()
        
        # Показать позиции
        for p in open_positions:
            symbol = p["symbol"]
            side = p["side"]
            size = float(p["size"])
            entry = float(p["avgPrice"])
            pnl = float(p.get("unrealisedPnl", 0))
            
            print(f"  {symbol:12} | {side:4} | Size: {size:10.6f} | Entry: {entry:10.4f} | PnL: {pnl:+10.4f}")
        
        print()
        print("⚠️  ВНИМАНИЕ: Все позиции будут закрыты по рынку!")
        print()
        
        confirm = input("Закрыть все позиции? (yes/no): ").strip().lower()
        
        if confirm != "yes":
            print("❌ Отменено")
            return
        
        print()
        print("Закрываю позиции...")
        print()
        
        # Закрыть каждую позицию
        closed = 0
        failed = 0
        
        for p in open_positions:
            symbol = p["symbol"]
            side = p["side"]
            size = p["size"]
            
            # Противоположная сторона для закрытия
            close_side = "Sell" if side == "Buy" else "Buy"
            
            try:
                resp = session.place_order(
                    category="linear",
                    symbol=symbol,
                    side=close_side,
                    orderType="Market",
                    qty=size,
                    reduceOnly=True,
                    timeInForce="IOC",
                )
                
                if resp.get("retCode") == 0:
                    print(f"  ✅ {symbol:12} | {side:4} | Закрыто")
                    closed += 1
                else:
                    print(f"  ❌ {symbol:12} | {side:4} | Ошибка: {resp.get('retMsg')}")
                    failed += 1
                    
            except Exception as e:
                print(f"  ❌ {symbol:12} | {side:4} | Исключение: {e}")
                failed += 1
        
        print()
        print("=" * 60)
        print(f"  Закрыто: {closed} | Ошибок: {failed}")
        print("=" * 60)
        
        if failed > 0:
            print()
            print("⚠️  Некоторые позиции не закрылись!")
            print("   Проверь их вручную на Bybit.com")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        print()
        print("Проверь:")
        print("  - API ключи правильные")
        print("  - Права: Contract Trade → Read + Write")
        print("  - Интернет соединение")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n❌ Прервано пользователем")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
    finally:
        input("\nНажми Enter для выхода...")
