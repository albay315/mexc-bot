# GitHub Actions ile Kurulum (ücretsiz, kart gerektirmez)

Bu bot artık **GitHub Actions** üzerinde çalışıyor: sunucu kiralamana gerek yok, GitHub
her 15 dakikada bir botu otomatik tetikliyor, taramayı yapıyor, sinyal varsa Telegram'a
gönderiyor ve kapanıyor.

## 1. Dosyaları repoya yükle

Bu depoda şu dosyalar/klasörler olmalı:

- `mexc_halftrend_bot.py`
- `requirements.txt`
- `.gitignore`
- `.github/workflows/scan.yml`  ← **bu dosya önemli**, GitHub Actions'ı tetikleyen dosya

`.github` klasörü gizli göründüğü için GitHub'ın web arayüzünden yüklerken dikkat et:
**Add file → Upload files** ile sürüklediğinde klasör yapısını (`.github/workflows/scan.yml`)
koruyarak yükler, ayrıca uğraşmana gerek yok.

## 2. Telegram bilgilerini GitHub Secrets'a ekle

1. Repo sayfasında **Settings** sekmesine git
2. Sol menüden **Secrets and variables → Actions**
3. **New repository secret** butonuna bas, sırasıyla ekle:
   - Name: `TELEGRAM_BOT_TOKEN` → Value: (BotFather'dan aldığın token)
   - Name: `TELEGRAM_CHAT_ID` → Value: (chat id'n)

## 3. Actions'ın repoya yazma izni olduğundan emin ol

1. **Settings → Actions → General**
2. Aşağı kaydır, **"Workflow permissions"** bölümünü bul
3. **"Read and write permissions"** seçeneğini işaretle
4. **Save**

(Bu izin, botun her taramadan sonra `state.json` dosyasını güncelleyip repoya
commit'leyebilmesi için gerekli — aksi halde bot aynı sinyali tekrar tekrar gönderebilir.)

## 4. İlk çalıştırmayı elle tetikle

1. Repo sayfasında **Actions** sekmesine git
2. Sol tarafta **"MEXC HalfTrend Scan"** workflow'unu seç
3. Sağ üstte **"Run workflow"** butonuna bas → tekrar **"Run workflow"** ile onayla
4. Birkaç saniye sonra listede yeni bir çalışma (run) belirir, üstüne tıkla
5. **"scan"** job'ına tıkla, adım adım logları görürsün

Logs'ta şunu görmelisin:
```
MEXC Futures HalfTrend + RSI taraması başlıyor...
... sembol taranıyor...
Tarama tamamlandı.
```

Yeşil tik ✅ görürsen her şey doğru çalışıyor demektir. Bundan sonra GitHub, cron
ayarına göre (her 15 dakikada bir) botu kendisi otomatik tetikleyecek — hiçbir şey
yapmana gerek yok.

## 5. Ayarları değiştirmek istersen

`mexc_halftrend_bot.py` dosyasının en üstündeki **AYARLAR** bölümünden:

| Parametre | Açıklama | Varsayılan |
|---|---|---|
| `TIMEFRAME` | Mum periyodu | `15m` |
| `AMPLITUDE` | HalfTrend lookback | `20` |
| `CHANNEL_DEVIATION` | HalfTrend ATR çarpanı | `2.0` |
| `RSI_PERIOD` | RSI periyodu | `14` |
| `RSI_BUY_MAX` | LONG için RSI üst sınırı | `30` |
| `RSI_SELL_MIN` | SHORT için RSI alt sınırı | `70` |
| `MIN_PRICE_USDT` | Minimum coin fiyatı | `20.0` |

Tarama sıklığını değiştirmek istersen `.github/workflows/scan.yml` içindeki
`cron: "*/15 * * * *"` satırını düzenle (örn. `*/5 * * * *` → her 5 dakikada bir).

## Önemli notlar

- GitHub Actions'ın **ücretsiz limiti**: public repo'larda sınırsız, private repo'larda
  ayda 2000 dakika (her tarama ~1 dakika sürer, günde 96 tarama x 30 gün ≈ 2880 dakika —
  15 dakikalık aralıkta private repo'da limite yaklaşabilirsin; limite takılırsan cron'u
  `*/20` veya `*/30` yaparak dakikayı düşürebilirsin, ya da repoyu public yapabilirsin).
- GitHub, **60 gün boyunca hiç commit/aktivite olmayan** repolardaki zamanlanmış (cron)
  workflow'ları otomatik durdurur. Botun kendi state commit'leri sayesinde bu genelde
  sorun olmaz, ama uzun süre sinyal çıkmazsa ara sıra **Actions** sekmesinden elle
  "Run workflow" ile kontrol etmekte fayda var.
- Zamanlanmış workflow'lar GitHub'ın yoğunluğuna göre birkaç dakika gecikebilir; kesin
  saniyesinde çalışacağının garantisi yoktur.
