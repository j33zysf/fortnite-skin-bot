"""Fetch the current Fortnite item shop and post the day's skins to a Discord channel.

Renders the whole shop as a single image: one row per outfit with a thumbnail, a
rarity-colored accent, the name, and rarity / price. Skins added recently get a NEW
badge and are pinned to the top. Posts the image via a Discord webhook (no always-on
bot needed). Designed to run daily (e.g. GitHub Actions cron) just after the shop
resets at 00:00 UTC.

Environment variables:
  DISCORD_WEBHOOK_URL  (required to post; if unset, writes shop_preview.png locally)
  NEW_DAYS             (optional, default 2) skins added within this many days get NEW
  FORTNITE_API_KEY     (optional) sent as Authorization header to fortnite-api.com
"""

import io
import json
import os
import urllib.request
import uuid
from datetime import datetime, timezone

from PIL import Image, ImageChops, ImageDraw, ImageFont

SHOP_URL = "https://fortnite-api.com/v2/shop"

# Layout / theme (Discord dark). Scaled up so text stays legible after Discord
# shrinks the image to fit the message column.
MAX_IMAGES = 2
WIDTH = 920
PAD = 26
HEADER_H = 78
ROW_H = 76
ICON = 56
BG = (43, 45, 49)
ROW_ALT = (47, 49, 54)
WHITE = (255, 255, 255)
MUTED = (181, 186, 193)
DIVIDER = (60, 63, 68)
BADGE_BG = (235, 69, 43)

RARITY_COLORS = {
    "common": (177, 177, 177),
    "uncommon": (96, 174, 63),
    "rare": (73, 174, 237),
    "epic": (185, 95, 244),
    "legendary": (234, 138, 53),
    "mythic": (230, 200, 79),
    "marvel": (237, 28, 36),
    "dc": (4, 118, 242),
    "star wars": (241, 196, 15),
    "icon": (61, 240, 224),
    "gaming": (44, 123, 229),
}
DEFAULT_COLOR = (114, 137, 218)

FONT_CANDIDATES_BOLD = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONT_CANDIDATES_REG = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


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
                "icon": images.get("icon") or images.get("smallIcon") or images.get("featured"),
                "price": price,
            }
    return outfits


def days_since(date_str, today):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (today - d).days
    except ValueError:
        return 10**6  # unparseable date sorts as very old


def rarity_color(o):
    haystack = f"{o.get('rarity_key', '')} {o.get('rarity', '')}".lower()
    for needle, color in RARITY_COLORS.items():
        if needle in haystack:
            return color
    return DEFAULT_COLOR


def load_font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def fetch_icon(url):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fortnite-skin-bot"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return Image.open(io.BytesIO(resp.read())).convert("RGBA")
    except Exception:
        return None


def rounded_icon(img, size, radius=9):
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    combined = ImageChops.multiply(img.split()[3], mask)
    img.putalpha(combined)
    return img


def render_image(items, today, total_count, new_total, page_idx, page_count):
    height = HEADER_H + ROW_H * len(items) + PAD
    img = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(img)

    f_title = load_font(FONT_CANDIDATES_BOLD, 34)
    f_name = load_font(FONT_CANDIDATES_BOLD, 26)
    f_detail = load_font(FONT_CANDIDATES_REG, 24)
    f_badge = load_font(FONT_CANDIDATES_BOLD, 17)

    # Header
    hy = HEADER_H // 2
    draw.text((PAD, hy), "Fortnite Item Shop", font=f_title, fill=WHITE, anchor="lm")
    title_w = draw.textlength("Fortnite Item Shop", font=f_title)
    sub = f"   {today:%b %d, %Y}  ·  {total_count} skins  ·  {new_total} new"
    draw.text((PAD + title_w, hy), sub, font=f_detail, fill=MUTED, anchor="lm")
    if page_count > 1:
        draw.text((WIDTH - PAD, hy), f"{page_idx}/{page_count}", font=f_detail,
                  fill=MUTED, anchor="rm")
    draw.line([(0, HEADER_H), (WIDTH, HEADER_H)], fill=DIVIDER, width=1)

    for i, o in enumerate(items):
        top = HEADER_H + i * ROW_H
        mid = top + ROW_H // 2
        if i % 2 == 1:
            draw.rectangle([0, top, WIDTH, top + ROW_H], fill=ROW_ALT)

        # Rarity accent bar on the left.
        draw.rounded_rectangle([PAD - 8, mid - ICON // 2, PAD - 2, mid + ICON // 2],
                               radius=3, fill=rarity_color(o))

        # Thumbnail.
        icon_x = PAD + 8
        icon_y = mid - ICON // 2
        icon = o.get("_icon_img")
        if icon is not None:
            img.paste(icon, (icon_x, icon_y), icon)
        else:
            draw.rounded_rectangle([icon_x, icon_y, icon_x + ICON, icon_y + ICON],
                                   radius=12, fill=(60, 63, 68))

        tx = icon_x + ICON + 18

        # NEW badge.
        if o["is_new"]:
            label = "NEW"
            bw = draw.textlength(label, font=f_badge) + 18
            draw.rounded_rectangle([tx, mid - 14, tx + bw, mid + 14], radius=14, fill=BADGE_BG)
            draw.text((tx + bw / 2, mid), label, font=f_badge, fill=WHITE, anchor="mm")
            tx += bw + 12

        # Name + details on one line.
        draw.text((tx, mid), o["name"], font=f_name, fill=WHITE, anchor="lm")
        tx += draw.textlength(o["name"], font=f_name)
        price = f"  ·  {o['price']:,} V-Bucks" if o.get("price") is not None else ""
        draw.text((tx, mid), f"   {o['rarity']}{price}", font=f_detail, fill=MUTED, anchor="lm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def post_image(webhook_url, png_bytes, content):
    boundary = "----fnbot" + uuid.uuid4().hex
    payload = json.dumps({"username": "Fortnite Shop", "content": content})
    body = b""
    body += (f"--{boundary}\r\n"
             'Content-Disposition: form-data; name="payload_json"\r\n\r\n'
             f"{payload}\r\n").encode()
    body += (f"--{boundary}\r\n"
             'Content-Disposition: form-data; name="files[0]"; filename="shop.png"\r\n'
             "Content-Type: image/png\r\n\r\n").encode()
    body += png_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "fortnite-skin-bot",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def main():
    new_days = int(os.environ.get("NEW_DAYS", "2"))
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    today = datetime.now(timezone.utc)

    shop = fetch_shop()
    if shop.get("status") != 200:
        raise SystemExit(f"Shop API returned status {shop.get('status')}")

    items = list(collect_outfits(shop).values())
    for o in items:
        o["is_new"] = days_since(o["added"], today) <= new_days
    items.sort(key=lambda o: (not o["is_new"], o["name"].lower()))

    for o in items:
        o["_icon_img"] = rounded_icon(fetch_icon(o["icon"]), ICON) if o.get("icon") else None

    total = len(items)
    new_total = sum(1 for o in items if o["is_new"])
    # Split across at most MAX_IMAGES images.
    per_image = -(-total // MAX_IMAGES) if total else 0  # ceil division
    pages = [items[i:i + per_image] for i in range(0, total, per_image)] or [[]]

    content = (f"\U0001f6d2 **Fortnite Item Shop — {today:%b %d, %Y}** · "
               f"{total} skins, {new_total} new")

    if not webhook_url:
        for idx, page in enumerate(pages, start=1):
            png = render_image(page, today, total, new_total, idx, len(pages))
            name = "shop_preview.png" if len(pages) == 1 else f"shop_preview_{idx}.png"
            with open(name, "wb") as fh:
                fh.write(png)
            print(f"[dry-run] Wrote {name} ({len(png):,} bytes, {len(page)} skins).")
        return

    status = None
    for idx, page in enumerate(pages, start=1):
        png = render_image(page, today, total, new_total, idx, len(pages))
        status = post_image(webhook_url, png, content if idx == 1 else "")
    print(f"Posted {len(pages)} image(s) to Discord ({total} skins, last HTTP {status}).")


if __name__ == "__main__":
    main()
