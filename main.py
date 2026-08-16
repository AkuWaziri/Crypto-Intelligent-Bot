"""
New Crypto Launch Alert Bot
Watches for brand-new token launches across DexScreener (catches almost every
chain/launchpad indirectly, since new tokens all end up as DEX pairs), pump.fun
(via PumpPortal's free real-time data feed), and Clanker launches on Farcaster.

IMPORTANT: This bot surfaces newly launched tokens for informational purposes only.
New launches are extremely high risk (rugs, honeypots, zero liquidity). This is NOT
financial advice and does no security/rug verification. Always DYOR.

Runs on a schedule via GitHub Actions (see .github/workflows/alert.yml)
"""

import os
import json
import time
import asyncio
import hashlib
import requests
from datetime import datetime, timezone, timedelta

try:
    import websockets
except ImportError:
    websockets = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
NEYNAR_API_KEY = os.environ.get("NEYNAR_API_KEY", "")

PUMP_LISTEN_SECONDS = int(os.environ.get("PUMP_LISTEN_SECONDS", "25"))
RECENCY_HOURS = 6  # new-launch bots care about very recent stuff only
SEEN_FILE = "seen_launches.json"
MAX_ALERTS_PER_RUN = 20  # safety cap so a burst of launches doesn't spam you


# ---------- HELPERS ----------
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    trimmed = list(seen)[-5000:]
    with open(SEEN_FILE, "w") as f:
        json.dump(trimmed, f)


def make_id(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(url, data=payload, timeout=15)
    if not r.ok:
        print("Telegram send failed:", r.text)


# ---------- DEXSCREENER (official, free, no key) ----------
def fetch_dexscreener_new():
    items = []
    now = datetime.now(timezone.utc)

    # newly submitted token profiles -- strong "new launch" signal, often includes socials
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            entries = data if isinstance(data, list) else data.get("tokens", [])
            for tok in entries[:25]:
                chain = tok.get("chainId", "unknown")
                addr = tok.get("tokenAddress", "")
                desc = (tok.get("description") or "").strip()
                links = tok.get("links", [])
                social_str = ", ".join(l.get("type", l.get("label", "")) for l in links) if links else "none listed"
                items.append({
                    "source": "DexScreener (new profile)",
                    "id": make_id("dsp", chain, addr),
                    "title": f"New token profile: {desc[:100] or addr[:12]} ({chain})",
                    "chain": chain,
                    "address": addr,
                    "link": tok.get("url", f"https://dexscreener.com/{chain}/{addr}"),
                    "detail": f"Socials: {social_str}",
                    "published": now,
                })
    except Exception as e:
        print(f"DexScreener token-profiles fetch failed: {e}")

    # freshly boosted/promoted tokens -- someone is paying to push visibility, worth knowing
    try:
        r = requests.get("https://api.dexscreener.com/token-boosts/latest/v1", headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            entries = data if isinstance(data, list) else data.get("tokens", [])
            for tok in entries[:25]:
                chain = tok.get("chainId", "unknown")
                addr = tok.get("tokenAddress", "")
                desc = (tok.get("description") or "").strip()
                amount = tok.get("amount", "")
                items.append({
                    "source": "DexScreener (boosted)",
                    "id": make_id("dsb", chain, addr, amount),
                    "title": f"Boosted/trending token: {desc[:100] or addr[:12]} ({chain})",
                    "chain": chain,
                    "address": addr,
                    "link": tok.get("url", f"https://dexscreener.com/{chain}/{addr}"),
                    "detail": f"Boost amount: {amount}",
                    "published": now,
                })
    except Exception as e:
        print(f"DexScreener token-boosts fetch failed: {e}")

    return items


# ---------- PUMP.FUN via PumpPortal (free, no key, real-time websocket) ----------
async def _listen_pumpportal(seconds):
    items = []
    if websockets is None:
        print("websockets library not installed -- skipping pump.fun feed")
        return items
    now = datetime.now(timezone.utc)
    try:
        async with websockets.connect("wss://pumpportal.fun/api/data", open_timeout=15) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            end_time = time.time() + seconds
            while time.time() < end_time:
                remaining = end_time - time.time()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                try:
                    data = json.loads(msg)
                except Exception:
                    continue
                mint = data.get("mint", "")
                name = data.get("name", "")
                symbol = data.get("symbol", "")
                if not mint or not name:
                    continue
                items.append({
                    "source": "pump.fun",
                    "id": make_id("pump", mint),
                    "title": f"New pump.fun launch: {name} (${symbol})",
                    "chain": "solana",
                    "address": mint,
                    "link": f"https://pump.fun/{mint}",
                    "detail": f"Creator: {data.get('traderPublicKey', 'unknown')[:12]}...",
                    "published": now,
                })
    except Exception as e:
        print(f"PumpPortal websocket fetch failed: {e}")
    return items


def fetch_pumpfun_new(seconds):
    try:
        return asyncio.run(_listen_pumpportal(seconds))
    except Exception as e:
        print(f"PumpPortal fetch failed: {e}")
        return []


# ---------- FARCASTER / CLANKER (via Neynar, catches Clanker launches) ----------
def fetch_clanker_launches(cutoff):
    items = []
    if not NEYNAR_API_KEY:
        return items
    try:
        r = requests.get(
            "https://api.neynar.com/v2/farcaster/cast/search",
            headers={"x-api-key": NEYNAR_API_KEY},
            params={"q": "clanker deploy", "limit": 20},
            timeout=15,
        )
        if not r.ok:
            print(f"Farcaster/Clanker fetch failed: HTTP {r.status_code}")
            return items
        data = r.json()
        casts = data.get("result", {}).get("casts", [])
        for cast in casts:
            cast_hash = cast.get("hash", "")
            ts = cast.get("timestamp", "")
            try:
                pub_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if pub_dt < cutoff:
                continue
            text = cast.get("text", "").strip()
            if not text:
                continue
            author = cast.get("author", {}).get("username", "unknown")
            items.append({
                "source": "Clanker (Farcaster)",
                "id": make_id("clanker", cast_hash),
                "title": f"Clanker launch mention: {text[:150]}",
                "chain": "base",
                "address": "",
                "link": f"https://warpcast.com/{author}/{cast_hash[:10]}",
                "detail": f"by @{author}",
                "published": pub_dt,
            })
    except Exception as e:
        print(f"Farcaster/Clanker fetch failed: {e}")
    return items


# ---------- FORMATTING ----------
def format_alert(item):
    lines = [
        f"🆕 <b>{item['title']}</b>",
        f"Platform: {item['source']} | Chain: {item.get('chain', 'n/a')}",
    ]
    if item.get("address"):
        lines.append(f"Contract: <code>{item['address']}</code>")
    if item.get("detail"):
        lines.append(item["detail"])
    lines.append(f"\n{item['link']}")
    lines.append("\n⚠️ New launch -- unverified, extremely high risk. Not financial advice. DYOR.")
    return "\n".join(lines)


# ---------- MAIN ----------
def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=RECENCY_HOURS)

    seen = load_seen()
    all_items = []

    dex_items = fetch_dexscreener_new()
    print(f"DexScreener: fetched {len(dex_items)} items")
    all_items.extend(dex_items)

    pump_items = fetch_pumpfun_new(PUMP_LISTEN_SECONDS)
    print(f"pump.fun: fetched {len(pump_items)} items (listened {PUMP_LISTEN_SECONDS}s)")
    all_items.extend(pump_items)

    clanker_items = fetch_clanker_launches(cutoff)
    print(f"Clanker/Farcaster: fetched {len(clanker_items)} items")
    all_items.extend(clanker_items)

    print(f"Total items fetched: {len(all_items)}")

    new_items = [it for it in all_items if it["id"] not in seen]
    print(f"New (unseen) items: {len(new_items)}")

    if not new_items:
        print("Nothing new this run.")
        return

    new_items.sort(key=lambda x: x["published"], reverse=True)
    to_send = new_items[:MAX_ALERTS_PER_RUN]

    for it in to_send:
        send_telegram(format_alert(it))
        seen.add(it["id"])
        time.sleep(1)

    # mark everything fetched this run as seen, even if not sent (respects the cap)
    for it in new_items:
        seen.add(it["id"])

    save_seen(seen)
    print(f"Sent {len(to_send)} new-launch alerts.")


if __name__ == "__main__":
    main()
