"""Fetch the current Fortnite item shop and post the day's skins to a Discord channel.

Shows every outfit in the shop as a compact card with a little icon, rarity-colored
stripe, and price; skins added recently are tagged 🆕 and pinned to the top. Posts via a
Discord webhook (no always-on bot process required). Designed to run on a daily schedule
(e.g. GitHub Actions cron) shortly after the shop resets at 00:00 UTC.

Environment variables:
  DISCORD_WEBHOOK_URL  (required to actually post; if unset, runs in dry-run/print mode)
  NEW_DAYS             (optional, default 2) skins added within this many days get the 🆕 tag
  FORTNITE_API_KEY     (optional) sent as Authorization header to fortnite-api.com
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

SHOP_URL = "https://fortnite-api.com/v2/shop"
EMBEDS_PER_MESSAGE = 10  # Discord's hard limit per webhook message
NEW_TAG = "\U0001f195"  # 🆕

# Rarity -> card stripe color (matched against the rarity's value/displayValue).
RARITY_COLORS = {
    "common": 0xB1B1B1,
    "uncommon": 0x60AE3F,
    "rare": 0x49AEED,
    "epic": 0xB95FF4,
    "legendary": 0xEA8A35,
    "mythic": 0xE6C84F,
    "marvel": 0xED1C24,
    "dc": 0x0476F2,
    "star wars": 0xF1C40F,
    "icon": 0x3DF0E0,
    "gaming": 0x2C7BE5,
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
            rarity = item.get("rarity") or {}
            outfits[item_id] = {
                "name": item.get("name", "Unknown"),
                "added": (item.get("added") or "")[:10],
                "rarity": rarity.get("displayValue", "Unknown"),
                "rarity_key": rarity.get("value", ""),
                "icon": images.get("smallIcon") or images.get("icon") or images.get("featured"),
                "price": price,
            }
    return outfits


def days_since(date_str, today):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (today - d).days
    except ValueError:
        return 10**6  # unparseable date sorts as very old


def pick_color(outfit):
    haystack = f"{outfit.get('rarity_key', '')} {outfit.get('rarity', '')}".lower()
    for needle, color in RARITY_COLORS.items():
        if needle in haystack:
            return color
    return DEFAULT_COLOR


def make_embed(o):
    name = f"{NEW_TAG} {o['name']}" if o["is_new"] else o["name"]
    price = f"  ·  {o['price']:,} V-Bucks" if o.get("price") is not None else ""
    embed = {
        "color": pick_color(o),
        "author": {"name": name},
        "description": f"{o['rarity']}{price}",
    }
    if o.get("icon"):
        embed["author"]["icon_url"] = o["icon"]
    return embed


def chunk(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def build_messages(outfits, new_days, today):
    items = list(outfits.values())
    for o in items:
        o["is_new"] = days_since(o["added"], today) <= new_days
    # New skins first, then everything else; alphabetical within each group.
    items.sort(key=lambda o: (not o["is_new"], o["name"].lower()))

    new_count = sum(1 for o in items if o["is_new"])
    header = (
        f"\U0001f6d2 **Fortnite Item Shop — {today:%b %d, %Y}**  ·  "
        f"{len(items)} skins, {new_count} new {NEW_TAG}"
    )

    messages = []
    for i, group in enumerate(chunk(items, EMBEDS_PER_MESSAGE) or [[]]):
        msg = {"username": "Fortnite Shop", "embeds": [make_embed(o) for o in group]}
        if i == 0:
            msg["content"] = header
        messages.append(msg)
    if not messages:
        messages.append({"username": "Fortnite Shop", "content": header + "\n_No skins found._"})
    return messages


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
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    today = datetime.now(timezone.utc)

    shop = fetch_shop()
    if shop.get("status") != 200:
        raise SystemExit(f"Shop API returned status {shop.get('status')}")

    messages = build_messages(collect_outfits(shop), new_days, today)

    if not webhook_url:
        print("[dry-run] DISCORD_WEBHOOK_URL not set. Messages that would be posted:\n")
        print(messages[0].get("content", ""))
        for msg in messages:
            for e in msg.get("embeds", []):
                icon = "[icon]" if e["author"].get("icon_url") else "[no icon]"
                print(f"  {icon} {e['author']['name']} — {e['description']}")
        return

    status = None
    for msg in messages:
        status = post_to_discord(webhook_url, msg)
    print(f"Posted {len(messages)} message(s) to Discord (last HTTP {status}).")


if __name__ == "__main__":
    main()
