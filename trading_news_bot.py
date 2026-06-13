"""
=============================================================
  TRADING NEWS BOT - Telegram
  Fokus: XAUUSD (Gold), Dollar (DXY), BTC
  Sumber: RSS FXEmpire + Investing.com + ForexFactory + CoinDesk
  Fitur: Auto-analisis dampak + Terjemahan Bahasa Indonesia
=============================================================

INSTALL DEPENDENCIES:
    pip install python-telegram-bot feedparser googletrans==4.0.0rc1 
               requests apscheduler anthropic python-dotenv

SETUP .env file:
    TELEGRAM_BOT_TOKEN=your_token_here
    TELEGRAM_CHANNEL_ID=@your_channel_or_chat_id
    ANTHROPIC_API_KEY=your_anthropic_key_here
"""

import asyncio
import feedparser
import requests
import json
import hashlib
import os
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
from telegram.constants import ParseMode
import anthropic
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ──────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID        = os.getenv("TELEGRAM_CHANNEL_ID")
ANTHROPIC_KEY     = os.getenv("ANTHROPIC_API_KEY")

# Interval cek berita (menit)
CHECK_INTERVAL    = 15

# File penyimpan berita yang sudah dikirim (hindari duplikat)
SENT_NEWS_FILE    = "sent_news.json"

# ─── RSS FEEDS (Multi-source) ─────────────────────────────────
RSS_FEEDS = [
    {
        "name": "FXEmpire",
        "url": "https://www.fxempire.com/api/v1/en/articles/rss",
        "icon": "📊"
    },
    {
        "name": "Investing.com",
        "url": "https://www.investing.com/rss/news_301.rss",
        "icon": "💹"
    },
    {
        "name": "CoinDesk (BTC)",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "icon": "₿"
    },
    {
        "name": "Reuters Markets",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "icon": "🌐"
    },
    {
        "name": "MarketWatch",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "icon": "📈"
    },
    {
        "name": "Bloomberg",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "icon": "🔵"
    },
]

# ─── KEYWORD FILTER (harus ada salah satu) ───────────────────
KEYWORDS_HIGH_IMPACT = [
    # Makro ekonomi
    "fed", "federal reserve", "interest rate", "rate hike", "rate cut",
    "inflation", "cpi", "ppi", "gdp", "nonfarm", "nfp", "payroll",
    "fomc", "powell", "ecb", "boj",
    # Geopolitik
    "iran", "israel", "russia", "ukraine", "war", "conflict", "sanction",
    # Gold
    "gold", "xauusd", "safe haven", "bullion",
    # Dollar
    "dollar", "usd", "dxy", "dollar index",
    # BTC / Crypto
    "bitcoin", "btc", "crypto", "cryptocurrency", "etf bitcoin",
    "blackrock bitcoin", "sec crypto",
    # Oil (mempengaruhi inflasi & gold)
    "oil", "crude", "opec", "wti", "brent",
]

KEYWORDS_MEDIUM_IMPACT = [
    "market", "rally", "selloff", "breakout", "resistance", "support",
    "bank", "central bank", "treasury", "yield", "bond",
    "recession", "growth", "employment", "jobs",
]


# ─── FUNGSI UTILITAS ─────────────────────────────────────────

def load_sent_news():
    """Load daftar berita yang sudah dikirim"""
    if os.path.exists(SENT_NEWS_FILE):
        with open(SENT_NEWS_FILE, "r") as f:
            data = json.load(f)
            # Hapus berita lebih dari 24 jam
            cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
            return {k: v for k, v in data.items() if v > cutoff}
    return {}


def save_sent_news(sent: dict):
    """Simpan daftar berita yang sudah dikirim"""
    with open(SENT_NEWS_FILE, "w") as f:
        json.dump(sent, f)


def get_news_id(title: str) -> str:
    """Buat ID unik dari judul berita"""
    return hashlib.md5(title.encode()).hexdigest()


def is_relevant(title: str, summary: str = "") -> tuple[bool, str]:
    """
    Cek apakah berita relevan dengan trading pairs kita.
    Return: (is_relevant, impact_level)
    """
    text = (title + " " + summary).lower()
    
    for kw in KEYWORDS_HIGH_IMPACT:
        if kw in text:
            return True, "HIGH"
    
    for kw in KEYWORDS_MEDIUM_IMPACT:
        if kw in text:
            return True, "MEDIUM"
    
    return False, "LOW"


def get_impact_emoji(level: str) -> str:
    return {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(level, "⚪")


# ─── AI ANALYSIS (Claude) ────────────────────────────────────

def analyze_impact_with_ai(title: str, summary: str, source: str) -> dict:
    """
    Gunakan Claude AI untuk analisis dampak pada Gold, Dollar, BTC
    Return dict dengan analisis per pair
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    
    prompt = f"""
Kamu adalah analis trading profesional. Analisis berita berikut dan berikan dampaknya pada XAUUSD (Gold), Dollar Index (DXY), dan Bitcoin (BTC).

BERITA:
Judul: {title}
Sumber: {source}
Ringkasan: {summary}

Berikan respons dalam format JSON seperti ini (HANYA JSON, tidak ada teks lain):
{{
  "judul_id": "terjemahan judul dalam Bahasa Indonesia",
  "ringkasan_id": "ringkasan berita dalam Bahasa Indonesia (2-3 kalimat)",
  "dampak_overall": "HIGH/MEDIUM/LOW",
  "gold": {{
    "arah": "BULLISH/BEARISH/NEUTRAL",
    "kekuatan": "KUAT/SEDANG/LEMAH",
    "alasan": "Penjelasan singkat kenapa gold naik/turun/sideways (1-2 kalimat)",
    "level_penting": "Level harga penting yang perlu diperhatikan jika ada, atau kosong"
  }},
  "dollar": {{
    "arah": "BULLISH/BEARISH/NEUTRAL",
    "kekuatan": "KUAT/SEDANG/LEMAH",
    "alasan": "Penjelasan singkat kenapa dollar naik/turun/sideways (1-2 kalimat)",
    "level_penting": ""
  }},
  "btc": {{
    "arah": "BULLISH/BEARISH/NEUTRAL",
    "kekuatan": "KUAT/SEDANG/LEMAH",
    "alasan": "Penjelasan singkat kenapa BTC naik/turun/sideways (1-2 kalimat)",
    "level_penting": ""
  }},
  "saran_trading": "Saran singkat untuk trader (maksimal 2 kalimat)",
  "tags": ["tag1", "tag2"]
}}
"""
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    raw = response.content[0].text.strip()
    # Bersihkan jika ada markdown backtick
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ─── FORMAT PESAN TELEGRAM ───────────────────────────────────

def format_telegram_message(news: dict, analysis: dict, source_icon: str, source_name: str) -> str:
    """Format pesan Telegram yang rapi dan informatif"""
    
    impact = analysis.get("dampak_overall", "MEDIUM")
    impact_emoji = get_impact_emoji(impact)
    
    gold   = analysis.get("gold", {})
    dollar = analysis.get("dollar", {})
    btc    = analysis.get("btc", {})
    
    def arah_emoji(arah):
        return {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}.get(arah, "➡️")
    
    def kekuatan_bar(kekuatan):
        return {"KUAT": "●●●", "SEDANG": "●●○", "LEMAH": "●○○"}.get(kekuatan, "●○○")
    
    tags = " ".join([f"#{t}" for t in analysis.get("tags", [])])
    waktu = datetime.now().strftime("%d %b %Y • %H:%M WIB")
    
    msg = f"""
{impact_emoji} *BREAKING NEWS* {impact_emoji}
━━━━━━━━━━━━━━━━━━━━
{source_icon} *{source_name}* | Impact: *{impact}*

📰 *{analysis.get('judul_id', news.get('title', ''))}*

{analysis.get('ringkasan_id', '')}

━━━━━━━━━━━━━━━━━━━━
📊 *ANALISIS DAMPAK MARKET*
━━━━━━━━━━━━━━━━━━━━

🥇 *GOLD (XAUUSD)*
{arah_emoji(gold.get('arah'))} {gold.get('arah')} {kekuatan_bar(gold.get('kekuatan'))}
_{gold.get('alasan', '-')}_
{f"🎯 Level: `{gold.get('level_penting')}`" if gold.get('level_penting') else ''}

💵 *DOLLAR (DXY)*
{arah_emoji(dollar.get('arah'))} {dollar.get('arah')} {kekuatan_bar(dollar.get('kekuatan'))}
_{dollar.get('alasan', '-')}_
{f"🎯 Level: `{dollar.get('level_penting')}`" if dollar.get('level_penting') else ''}

₿ *BITCOIN (BTC)*
{arah_emoji(btc.get('arah'))} {btc.get('arah')} {kekuatan_bar(btc.get('kekuatan'))}
_{btc.get('alasan', '-')}_
{f"🎯 Level: `{btc.get('level_penting')}`" if btc.get('level_penting') else ''}

━━━━━━━━━━━━━━━━━━━━
💡 *SARAN:* _{analysis.get('saran_trading', '-')}_

{tags}
🕐 {waktu}
━━━━━━━━━━━━━━━━━━━━
⚠️ _Not Financial Advice • DYOR_
""".strip()
    
    return msg


# ─── MAIN BOT LOGIC ──────────────────────────────────────────

async def fetch_and_send_news():
    """Ambil berita terbaru dan kirim ke Telegram"""
    bot = Bot(token=TELEGRAM_TOKEN)
    sent_news = load_sent_news()
    new_sent = dict(sent_news)
    
    logging.info(f"[{datetime.now().strftime('%H:%M')}] Mengecek berita baru...")
    
    for feed_config in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_config["url"])
            
            for entry in feed.entries[:10]:  # Ambil 10 berita terbaru per feed
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                link    = entry.get("link", "")
                
                # Skip jika sudah dikirim
                news_id = get_news_id(title)
                if news_id in sent_news:
                    continue
                
                # Cek relevansi
                relevant, impact = is_relevant(title, summary)
                if not relevant:
                    continue
                
                # Skip berita impact LOW
                if impact == "LOW":
                    continue
                
                logging.info(f"Berita baru ditemukan [{impact}]: {title[:60]}...")
                
                # Analisis dengan AI
                try:
                    analysis = analyze_impact_with_ai(title, summary, feed_config["name"])
                except Exception as e:
                    logging.error(f"AI analysis gagal: {e}")
                    # Fallback tanpa AI
                    analysis = {
                        "judul_id": title,
                        "ringkasan_id": summary[:200] if summary else "-",
                        "dampak_overall": impact,
                        "gold":   {"arah": "NEUTRAL", "kekuatan": "SEDANG", "alasan": "Analisis sedang tidak tersedia", "level_penting": ""},
                        "dollar": {"arah": "NEUTRAL", "kekuatan": "SEDANG", "alasan": "Analisis sedang tidak tersedia", "level_penting": ""},
                        "btc":    {"arah": "NEUTRAL", "kekuatan": "SEDANG", "alasan": "Analisis sedang tidak tersedia", "level_penting": ""},
                        "saran_trading": "Pantau pergerakan market dan tunggu konfirmasi.",
                        "tags": ["news", "trading"]
                    }
                
                # Format dan kirim pesan
                message = format_telegram_message(
                    {"title": title, "link": link},
                    analysis,
                    feed_config["icon"],
                    feed_config["name"]
                )
                
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
                
                # Tandai sudah dikirim
                new_sent[news_id] = datetime.now().isoformat()
                
                # Jeda antar pesan agar tidak spam
                await asyncio.sleep(3)
                
        except Exception as e:
            logging.error(f"Error feed {feed_config['name']}: {e}")
            continue
    
    save_sent_news(new_sent)
    logging.info("Selesai mengecek berita.")


async def send_daily_summary():
    """Kirim ringkasan harian setiap pagi jam 07:00 WIB"""
    bot = Bot(token=TELEGRAM_TOKEN)
    
    tanggal = datetime.now().strftime("%A, %d %B %Y")
    
    summary_msg = f"""
🌅 *SELAMAT PAGI, TRADER!*
━━━━━━━━━━━━━━━━━━━━
📅 {tanggal}

Bot News Trading aktif memantau:
🥇 XAUUSD (Gold)
💵 Dollar Index (DXY)
₿ Bitcoin (BTC)

📡 *Sumber aktif:*
• FXEmpire
• Investing.com
• CoinDesk
• Reuters Markets
• MarketWatch
• Bloomberg

⏰ *Sesi hari ini:*
• 🇦🇺 Sydney   : 05:00 - 14:00 WIB
• 🇯🇵 Tokyo    : 07:00 - 16:00 WIB  
• 🇬🇧 London   : 14:00 - 23:00 WIB
• 🇺🇸 New York : 19:00 - 04:00 WIB

💡 _Pantau berita HIGH IMPACT untuk peluang terbaik!_
━━━━━━━━━━━━━━━━━━━━
⚠️ _Not Financial Advice • DYOR_
""".strip()
    
    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=summary_msg,
        parse_mode=ParseMode.MARKDOWN
    )


# ─── SCHEDULER & MAIN ────────────────────────────────────────

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    print("=" * 50)
    print("  TRADING NEWS BOT - Starting...")
    print(f"  Channel  : {CHANNEL_ID}")
    print(f"  Interval : setiap {CHECK_INTERVAL} menit")
    print("=" * 50)
    
    # Jalankan sekali saat start
    await fetch_and_send_news()
    
    # Setup scheduler
    scheduler = AsyncIOScheduler(timezone="Asia/Jakarta")
    
    # Cek berita setiap 15 menit
    scheduler.add_job(
        fetch_and_send_news,
        "interval",
        minutes=CHECK_INTERVAL,
        id="news_checker"
    )
    
    # Kirim daily summary setiap jam 07:00 WIB
    scheduler.add_job(
        send_daily_summary,
        "cron",
        hour=7, minute=0,
        id="daily_summary"
    )
    
    scheduler.start()
    print("Bot berjalan! Tekan Ctrl+C untuk berhenti.\n")
    
    # Jaga agar program tetap berjalan
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("\nBot dihentikan.")


if __name__ == "__main__":
    asyncio.run(main())
