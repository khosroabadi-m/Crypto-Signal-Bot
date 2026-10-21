# Khosro Crypto Signal Bot — GitHub Repo

این ریپو شامل **موتور تحلیل سیگنال** است که در **GitHub Actions** هر ۳۰ دقیقه اجرا می‌شود.

> 📡 **معماری:** این ریپو فقط موتور تحلیل است. **مانیتور زنده** و **داشبورد وب** روی سرور [Wispbyte](../wispbyte-server) اجرا می‌شوند.

---

## 📁 ساختار

```
Crypto-Signal-Bot/
├── .github/workflows/
│   ├── signal-bot.yml         # هر ۳۰ دقیقه: تحلیل + commit + تلگرام
│   └── nightly.yml           # شبانه: گزارش روز قبل به تلگرام
├── engine/
│   ├── main.py                # موتور تحلیل (۱۰ مؤلفه + Groq AI)
│   └── daily_report.py       # گزارش روزانه تلگرام
├── signals.json              # خروجی (commit خودکار توسط Actions)
├── trade_history.json         # تاریخچه معاملات (commit خودکار)
├── requirements.txt
└── README.md
```

---

## 🔑 Secrets مورد نیاز (Repository Settings → Secrets and variables → Actions)

| Secret | توضیح | ضروری؟ |
|--------|-------|--------|
| `TELEGRAM_BOT_TOKEN` | توکن ربات تلگرام (از @BotFather) | برای ارسال تلگرام |
| `TELEGRAM_CHAT_ID` | Chat ID مقصد (از @userinfobot) | برای ارسال تلگرام |
| `GROQ_API_KEY` | کلید Groq AI ([console.groq.com](https://console.groq.com/keys)) | اختیاری (بدون آن rule-only) |

---

## ⚙️ نحوه کار

1. **هر ۳۰ دقیقه** GitHub Actions اجرا می‌شود
2. موتور `engine/main.py` اجرا می‌شود:
   - ۵ نماد (BTC, ETH, SOL, BNB, XRP) را از Binance می‌گیرد
   - هر کدام را برای LONG و SHORT تحلیل می‌کند
   - اگر امتیاز از threshold عبور کند → سیگنال
   - (اختیاری) Groq AI رأی می‌دهد
3. خروجی در `signals.json` ذخیره و commit می‌شود
4. پیام تلگرام ارسال می‌شود
5. سرور Wispbyte که همیشه روشن است، فایل `signals.json` را می‌خواند و:
   - WebSocket Binance را پایش می‌کند
   - TP/SL را تشخیص می‌دهد
   - نتایج را به `trade_history.json` (که به همین ریپو push می‌شود) اضافه می‌کند
   - داشبورد وب را نمایش می‌دهد
   - ربات تلگرام polling

---

## 🧪 تست محلی

```bash
# نصب
pip install -r requirements.txt

# اجرای موتور
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=xxx GROQ_API_KEY=xxx python engine/main.py

# گزارش روزانه (فقط چاپ)
python engine/daily_report.py --print
```

---

## 📊 موتور تحلیل (نسخه سبک)

این موتور نسخه سبک‌شده‌ی KhosroSignalAnalyzerBot v11.3 است:

| مؤلفه | وزن |
|-------|-----|
| AI Committee (Groq) | 25 |
| Order Flow | 14 |
| Volume Profile | 11 |
| Sweep | 10 |
| Moving Average (EMA21/55) | 5 |
| MACD | 4 |
| RSI | 3 |
| **مجموع** | **72** (28 از 100 حذف شده برای سبکی) |

سقف‌های ریسک میراث v11.3:
- Stop Max: 3٪
- Stop Min: 0.5٪
- Struct Stop Max ATR: 2.5×
- Signal Max Age: 96 ساعت
- Long Threshold: 62
- Short Threshold: 72 (سخت‌گیرانه‌تر)
- Short HTF Min: 0.60

---

## 🔗 ارتباط با سرور Wispbyte

سرور Wispbyte فایل `signals.json` این ریپو را از طریق GitHub raw URL می‌خواند:

```
https://raw.githubusercontent.com/khosroabadi-m/Crypto-Signal-Bot/main/signals.json
```

و وقتی معامله‌ای بسته می‌شود (TP/SL)، نتیجه را به `trade_history.json` این ریپو اضافه می‌کند (با GitHub API).

برای راه‌اندازی سرور Wispbyte، به پوشه `../wispbyte-server` بروید.
