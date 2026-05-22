"""Fetch the current Fortnite item shop and post the newest skins to a Discord channel.

Posts via a Discord webhook (no always-on bot process required). Designed to run on a
daily schedule (e.g. GitHub Actions cron) shortly after the shop resets at 00:00 UTC.

Environment variables:
  DISCORD_WEBHOOK_URL  (required to actually post; if unset, runs in dry-run/print mode)
  NEW_DAYS             (optional, default 2) skins added within this many days = "brand new"
  MAX_SKINS            (optional, default 10) max skins to show (Discord caps embeds at 10)
  FORTNITE_API_KEY     (optional) sent as Authorization header to fortnite-api.com
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

SHOP_URL = "https://fortnite-api.com/v2/shop"

# Rarity -> embed sidebar color (hex as int). Falls back to a neutral gray.
RARITY_COLORS = {
    "common": 0xB1B1B1,
    "uncommon": 0x60AE3F,
    "rare": 0x49AEED,
    "epic": 0xB95FF4,
    "legendary": 0xEA8A35,
    "mythic": 0xE6C84F,
    "marvel": 0xED1C24,
    "dc": 0x2980B9,
    "icon": 0x3DF0E0,
    "gaming": 0x2C7BE5,
    "star wars": 0xF1C40F,
}
DEFAULT_COLOR = 0x7289DA


def fetch_shop():
    headers = {"User-Agent": "fortnite-skin-bot"}
    api_key = os.environ.get("FORTNITE_API_KEY")
    if api_key:
        headers["Authorization"] = api_key
    req = urllib.request.Request(SHOP_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def collect_outfits(shop):
    """Return a dict id -> outfit info for every outfit currently in the shop."""
    outfits = {}
    for entry in shop["data"].get("entries", []):
        price = entry.get("finalPrice")
        for item in entry.get("brItems") or []:
            if item.get("type", {}).get("value") != "outfit":
                continue
            item_id = item["id"]
            if item_id in outfits:
                continue  # keep first occurrence (dedupe across bundles)
            images = item.get("images") or {}
            outfits[item_id] = {
                "name": item.get("name", "Unknown"),
                "added": (item.get("added") or "")[:10],
                "rarity": (item.get("rarity") or {}).get("displayValue", "Unknown"),
                "rarity_key": (item.get("rarity") or {}).get("value", ""),
                "icon": images.get("icon") or images.get("featured") or images.get("smallIcon"),
                "price": price,
                "series": (item.get("series") or {}).get("value"),
            }
    return outfits


def days_since(date_str, today):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (today - d).days
    except ValueError:
        return 10**6  # unparseable date sorts as very old


def pick_color(outfit):
    key = (outfit.get("rarity_key") or "").lower()
    name = (outfit.get("rarity") or "").lower()
    for needle, color in RARITY_COLORS.items():
        if needle in key or needle in name:
            return color
    return DEFAULT_COLOR


def build_payload(outfits, new_days, max_skins, today):
    ordered = sorted(outfits.values(), key=lambda o: o["added"], reverse=True)
    brand_new = [o for o in ordered if days_since(o["added"], today) <= new_days]

    if brand_new:
        chosen = brand_new[:max_skins]
        header = f"🛒 **{len(brand_new)} new skin(s) in the Fortnite shop today** ({today:%b %d, %Y})"
    else:
        chosen = ordered[:max_skins]
        header = (
            f"🛒 **No brand-new skins today** ({today:%b %d, %Y}) — "
            "here are the most recently added ones in the shop:"
        )

    embeds = []
    for o in chosen:
        fields = [{"name": "Rarity", "value": o["rarity"], "inline": True}]
        if o.get("price") is not None:
            fields.append({"name": "Price", "value": f"{o['price']:,} V-Bucks", "inline": True})
        embed = {
            "title": o["name"],
            "color": pick_color(o),
            "fields": fields,
            "footer": {"text": f"Added {o['added']}"},
        }
        if o.get("icon"):
            embed["thumbnail"] = {"url": o["icon"]}
        embeds.append(embed)

    return {
        "username": "Fortnite Shop",
        "content": header,
        "embeds": embeds,
    }


def post_to_discord(webhook_url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "fortnite-skin-bot"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # so emoji print works on Windows consoles
    except (AttributeError, ValueError):
        pass
    new_days = int(os.environ.get("NEW_DAYS", "2"))
    max_skins = max(1, min(10, int(os.environ.get("MAX_SKINS", "10"))))
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    today = datetime.now(timezone.utc)

    shop = fetch_shop()
    if shop.get("status") != 200:
        raise SystemExit(f"Shop API returned status {shop.get('status')}")

    outfits = collect_outfits(shop)
    payload = build_payload(outfits, new_days, max_skins, today)

    if not webhook_url:
        print("[dry-run] DISCORD_WEBHOOK_URL not set. Payload that would be posted:\n")
        print(payload["content"])
        for e in payload["embeds"]:
            price = next((f["value"] for f in e["fields"] if f["name"] == "Price"), "—")
            print(f"  - {e['title']} | {e['fields'][0]['value']} | {price} | {e['footer']['text']}")
        return

    status = post_to_discord(webhook_url, payload)
    print(f"Posted {len(payload['embeds'])} skin(s) to Discord (HTTP {status}).")


if __name__ == "__main__":
    main()
