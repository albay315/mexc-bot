"""
MEXC Futures HalfTrend + RSI Sinyal Botu (tek dosya)
=====================================================

Bu bot EMİR AÇMAZ — sadece taradığı MEXC Futures (USDT-M perpetual) coinlerinde
HalfTrend [BigBeluga] indikatörünün trend dönüşü sinyali ile RSI filtresini
birleştirip uygun olduğunda Telegram'a bildirim gönderir.

Sinyal mantığı:
  - LONG  : HalfTrend trendi SHORT -> LONG döndüğünde  VE  RSI(14) < 30
  - SHORT : HalfTrend trendi LONG  -> SHORT döndüğünde VE  RSI(14) > 70

Filtreler:
  - Sadece MEXC Futures (swap), USDT paritesi
  - Fiyatı 20 USDT altında olan coinler taranmaz (MIN_PRICE_USDT)
  - Altın/gümüş benzeri (XAUT, PAXG, GOLD, SILVER...), tokenize hisse benzeri
    (STOCK, PRE, EQUITY...) ve stabilcoin bazlı semboller elenir.

Kurulum:
    pip install ccxt pandas numpy requests python-dotenv

Çalıştırma:
    export TELEGRAM_BOT_TOKEN="123456789:AA..."
    export TELEGRAM_CHAT_ID="123456789"
    python mexc_halftrend_bot.py

(İstersen bu iki değişkeni .env dosyasına da koyabilirsin, otomatik okunur.)
"""

import json
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import ccxt

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ══════════════════════════════════════════════════════════════════════
# AYARLAR
# ══════════════════════════════════════════════════════════════════════

# ── Telegram ─────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Borsa ────────────────────────────────────────────────────────────
EXCHANGE_ID = "mexc"
MARKET_TYPE = "swap"     # MEXC Futures (perpetual) piyasaları
QUOTE_CURRENCY = "USDT"

# ── HalfTrend indikatör parametreleri (orijinal Pine Script ile aynı) ─
AMPLITUDE = 20
CHANNEL_DEVIATION = 2.0
ATR_PERIOD = 100

# ── RSI ──────────────────────────────────────────────────────────────
RSI_PERIOD = 14
RSI_BUY_MAX = 30     # LONG sinyali için RSI bunun altında olmalı
RSI_SELL_MIN = 70    # SHORT sinyali için RSI bunun üstünde olmalı

# Her taramada, state'te kaldığı yerden itibaren en fazla kaç mum geriye
# bakılıp kontrol edilsin (bot bir süre çalışmazsa / gecikirse sinyal
# kaçmasın diye güvenlik payı).
CHECK_LAST_N_CANDLES = 4

# ── Tarama ayarları ─────────────────────────────────────────────────
TIMEFRAME = "15m"            # mum periyodu (1m, 5m, 15m, 1h, 4h ...)
OHLCV_LIMIT = 300            # her taramada çekilecek mum sayısı
MIN_PRICE_USDT = 20.0        # bu fiyatın altındaki coinler taranmaz
POLL_INTERVAL_SECONDS = 60   # her tam tarama arası bekleme (sn)
RATE_LIMIT_SLEEP = 0.25      # sembol başına istekler arası ek bekleme (sn)

# Altın/gümüş/hisse benzeri veya stabilcoin gibi istenmeyen varlıkları
# base sembolde arayarak eleyen anahtar kelimeler.
BLACKLIST_KEYWORDS = [
    "XAUT", "PAXG", "GOLD", "XAG", "SILVER",          # altın/gümüş tokenleri
    "STOCK", "PRE", "EQUITY",                          # tokenize hisse benzeri
    "USDC", "USDT", "BUSD", "TUSD", "DAI", "FDUSD",    # stabilcoinler
]

STATE_FILE = "state.json"


# ══════════════════════════════════════════════════════════════════════
# İNDİKATÖRLER (HalfTrend + RSI)
# ══════════════════════════════════════════════════════════════════════

def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def compute_halftrend(df: pd.DataFrame, amplitude: int, channel_deviation: float,
                       atr_period: int = 100) -> pd.DataFrame:
    """
    HalfTrend (BigBeluga / everget mantığı), orijinal Pine Script'teki gibi
    bar bar (stateful) hesaplanır.
    """
    df = df.copy().reset_index(drop=True)
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(df)

    atr2 = (compute_atr(df, atr_period) / 2).values
    high_roll = df["high"].rolling(amplitude).max().values
    low_roll = df["low"].rolling(amplitude).min().values
    highma = df["high"].rolling(amplitude).mean().values
    lowma = df["low"].rolling(amplitude).mean().values

    trend = [0] * n
    next_trend = [0] * n
    max_low = [np.nan] * n
    min_high = [np.nan] * n
    up = [np.nan] * n
    down = [np.nan] * n
    buy_signal = [False] * n
    sell_signal = [False] * n

    initialized = False

    for i in range(n):
        if np.isnan(atr2[i]) or np.isnan(high_roll[i]) or np.isnan(low_roll[i]):
            continue

        if not initialized:
            max_low[i] = low_roll[i]
            min_high[i] = high_roll[i]
            trend[i] = 0
            next_trend[i] = 0
            up[i] = max_low[i]
            down[i] = 0.0
            initialized = True
            continue

        # önceki durumu taşı
        max_low[i] = max_low[i - 1]
        min_high[i] = min_high[i - 1]
        trend[i] = trend[i - 1]
        next_trend[i] = next_trend[i - 1]
        up[i] = up[i - 1]
        down[i] = down[i - 1]

        prev_low = low[i - 1]
        prev_high = high[i - 1]

        if next_trend[i] == 1:
            max_low[i] = max(low_roll[i], max_low[i])
            if highma[i] < max_low[i] and close[i] < prev_low:
                trend[i] = 1
                next_trend[i] = 0
                min_high[i] = high_roll[i]
        else:
            min_high[i] = min(high_roll[i], min_high[i])
            if lowma[i] > min_high[i] and close[i] > prev_high:
                trend[i] = 0
                next_trend[i] = 1
                max_low[i] = low_roll[i]

        if trend[i] == 0:
            if trend[i - 1] != 0:
                up[i] = down[i - 1]
            else:
                up[i] = max(max_low[i], up[i - 1])
        else:
            if trend[i - 1] != 1:
                down[i] = up[i - 1]
            else:
                down[i] = min(min_high[i], down[i - 1])

        buy_signal[i] = trend[i] == 0 and trend[i - 1] == 1
        sell_signal[i] = trend[i] == 1 and trend[i - 1] == 0

    df["trend"] = trend
    df["ht_line"] = [u if t == 0 else d for u, d, t in zip(up, down, trend)]
    df["buy_signal"] = buy_signal
    df["sell_signal"] = sell_signal
    return df


# ══════════════════════════════════════════════════════════════════════
# BORSA (MEXC Futures)
# ══════════════════════════════════════════════════════════════════════

def get_exchange():
    exchange_class = getattr(ccxt, EXCHANGE_ID)
    return exchange_class({
        "enableRateLimit": True,
        "options": {"defaultType": MARKET_TYPE},
    })


def get_valid_symbols(exchange) -> list:
    """
    MEXC Futures'ta işlem gören, USDT paritesinde, fiyatı MIN_PRICE_USDT
    üzerinde olan ve blacklist'e girmeyen (altın/gümüş/stabilcoin/hisse
    benzeri) sembollerin listesini döner.
    """
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    symbols = []
    for symbol, market in markets.items():
        if not market.get("swap", False):
            continue
        if market.get("quote") != QUOTE_CURRENCY:
            continue
        if not market.get("active", True):
            continue

        base = (market.get("base") or "").upper()
        if any(kw in base for kw in BLACKLIST_KEYWORDS):
            continue

        ticker = tickers.get(symbol)
        if not ticker or ticker.get("last") in (None, 0):
            continue
        if ticker["last"] < MIN_PRICE_USDT:
            continue

        symbols.append(symbol)

    return sorted(symbols)


def fetch_ohlcv_df(exchange, symbol: str, timeframe: str, limit: int):
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception as e:
        print(f"[uyarı] {symbol} için OHLCV alınamadı: {e}")
        return None

    if not raw or len(raw) < 5:
        return None

    return pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])


# ══════════════════════════════════════════════════════════════════════
# TELEGRAM BİLDİRİMİ
# ══════════════════════════════════════════════════════════════════════

def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[uyarı] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış, mesaj gönderilmiyor.")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"[uyarı] Telegram gönderim hatası: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[uyarı] Telegram isteği başarısız: {e}")


def format_signal_message(symbol: str, side: str, price: float, rsi: float, timeframe: str) -> str:
    emoji = "🟢" if side == "LONG" else "🔴"
    return (
        f"{emoji} <b>{side} sinyali</b>\n"
        f"Sembol: <b>{symbol}</b>\n"
        f"Zaman dilimi: {timeframe}\n"
        f"Fiyat: {price:.6f} USDT\n"
        f"RSI({RSI_PERIOD}): {rsi:.2f}\n"
        f"Kaynak: HalfTrend [BigBeluga]"
    )


# ══════════════════════════════════════════════════════════════════════
# DURUM (aynı mum için tekrar bildirim göndermemek için)
# ══════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ══════════════════════════════════════════════════════════════════════
# ANA TARAMA DÖNGÜSÜ
# ══════════════════════════════════════════════════════════════════════

def analyze_symbol(exchange, symbol: str, state: dict) -> None:
    """
    Her çalıştırmada sadece son kapanan mumu değil, state'te kaldığı yerden
    (en son incelenen mumdan) itibaren son CHECK_LAST_N_CANDLES mumu tarar.
    Böylece GitHub Actions cron'u gecikirse veya bir çalışma atlanırsa,
    aradaki mumlarda oluşan sinyaller kaçırılmaz.
    """
    df = fetch_ohlcv_df(exchange, symbol, TIMEFRAME, OHLCV_LIMIT)
    if df is None or len(df) < AMPLITUDE + ATR_PERIOD:
        return

    # Son mum henüz kapanmamış olabilir; sadece kapanmış mumları kullan.
    closed_df = df.iloc[:-1].copy()
    if len(closed_df) < AMPLITUDE + ATR_PERIOD:
        return

    closed_df = compute_halftrend(closed_df, AMPLITUDE, CHANNEL_DEVIATION, ATR_PERIOD)
    closed_df["rsi"] = compute_rsi(closed_df["close"], RSI_PERIOD)

    last_examined_ts = state.get(symbol, 0)
    candidates = closed_df[closed_df["timestamp"] > last_examined_ts].tail(CHECK_LAST_N_CANDLES)
    if candidates.empty:
        return

    newest_ts = int(closed_df.iloc[-1]["timestamp"])

    for _, row in candidates.iterrows():
        long_ok = bool(row["buy_signal"]) and row["rsi"] < RSI_BUY_MAX
        short_ok = bool(row["sell_signal"]) and row["rsi"] > RSI_SELL_MIN
        if not (long_ok or short_ok):
            continue

        side = "LONG" if long_ok else "SHORT"
        msg = format_signal_message(symbol, side, float(row["close"]), float(row["rsi"]), TIMEFRAME)
        send_telegram_message(msg)
        print(f"[sinyal] {symbol} -> {side} @ {row['close']} (RSI {row['rsi']:.2f}, "
              f"mum zamanı {datetime.fromtimestamp(row['timestamp']/1000, tz=timezone.utc).isoformat()})")

    state[symbol] = newest_ts


def run_once(exchange, symbols: list, state: dict) -> None:
    for symbol in symbols:
        try:
            analyze_symbol(exchange, symbol, state)
        except Exception as e:
            print(f"[hata] {symbol} işlenirken sorun oluştu: {e}")
        time.sleep(RATE_LIMIT_SLEEP)
    save_state(state)


def run_single_scan() -> None:
    """Tek seferlik tarama: tüm sembolleri kontrol eder, sinyal varsa yollar, state'i kaydeder ve çıkar.
    GitHub Actions gibi zamanlanmış (cron) ortamlar için kullanılır."""
    exchange = get_exchange()
    state = load_state()

    print(f"MEXC Futures HalfTrend + RSI taraması başlıyor "
          f"(timeframe={TIMEFRAME}, min_price={MIN_PRICE_USDT} USDT)")

    symbols = get_valid_symbols(exchange)
    print(f"[{datetime.now(timezone.utc).isoformat()}] {len(symbols)} sembol taranıyor...")
    run_once(exchange, symbols, state)
    print("Tarama tamamlandı.")


def run_loop() -> None:
    """Sürekli döngü modu: kendi sunucunda / bilgisayarında 7/24 çalıştırmak istersen
    LOOP=1 ortam değişkenini ayarla."""
    exchange = get_exchange()
    state = load_state()

    print(f"MEXC Futures HalfTrend + RSI sinyal botu başlıyor (döngü modu, "
          f"timeframe={TIMEFRAME}, min_price={MIN_PRICE_USDT} USDT)")

    while True:
        try:
            symbols = get_valid_symbols(exchange)
            print(f"[{datetime.now(timezone.utc).isoformat()}] {len(symbols)} sembol taranıyor...")
            run_once(exchange, symbols, state)
        except Exception as e:
            print(f"[hata] Tarama döngüsünde sorun: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    if os.getenv("LOOP", "0") == "1":
        run_loop()
    else:
        run_single_scan()
