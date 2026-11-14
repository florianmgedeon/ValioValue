"""Draw the demo documents in contracts/fixtures/uploads/ (order list, menu, allergy note, invoices PDF).

Generated, not photographed: good enough for a vision model and for the offline demo, where the scripted
provider reads the matching .json instead. Run: .venv/bin/python scripts/make_fixtures.py
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "contracts" / "fixtures" / "uploads"
SUPP = Path("/System/Library/Fonts/Supplemental")


def font(name: str, size: int, index: int = 0):
    for p in (SUPP / name, Path("/System/Library/Fonts") / name):
        if p.exists():
            return ImageFont.truetype(str(p), size, index=index)
    return ImageFont.load_default(size)


def paper(w: int, h: int, tint=(250, 248, 240), lines: bool = False) -> Image.Image:
    img = Image.new("RGB", (w, h), tint)
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    for _ in range(2500):  # paper grain
        x, y = rnd.randrange(w), rnd.randrange(h)
        c = rnd.randint(-8, 4)
        d.point((x, y), fill=tuple(max(0, min(255, v + c)) for v in tint))
    if lines:
        for y in range(160, h - 40, 74):
            d.line((40, y, w - 40, y), fill=(196, 214, 232), width=2)
        d.line((110, 0, 110, h), fill=(232, 170, 170), width=2)
    return img


def photo(img: Image.Image, angle: float) -> Image.Image:
    """Slight rotation on a dark table, as if photographed."""
    bg = Image.new("RGB", (img.width + 160, img.height + 160), (64, 58, 52))
    rot = img.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(64, 58, 52))
    bg.paste(rot, ((bg.width - rot.width) // 2, (bg.height - rot.height) // 2))
    return bg.filter(ImageFilter.GaussianBlur(0.6))


def order_list() -> None:
    img = paper(1100, 1400, lines=True)
    d = ImageDraw.Draw(img)
    hand = font("Bradley Hand Bold.ttf", 64)
    d.text((140, 60), "Order – next week", font=font("Bradley Hand Bold.ttf", 72), fill=(28, 40, 110))
    rows = ["milk 120 L", "butter 10 kg", "rye rolls 300", "apples 40 kg", "granola 15 kg", "", "by Monday morning!"]
    for n, row in enumerate(rows):
        d.text((150 + (n % 2) * 6, 180 + n * 148), row, font=hand, fill=(28, 40, 110))
    d.text((720, 1250), "– Anna", font=font("Bradley Hand Bold.ttf", 54), fill=(28, 40, 110))
    photo(img, -3.5).save(OUT / "order-list.jpg", quality=85)


def menu() -> None:
    img = paper(1240, 1600, tint=(255, 255, 255))
    d = ImageDraw.Draw(img)
    h1, h2, body, small = font("HelveticaNeue.ttc", 64, 1), font("HelveticaNeue.ttc", 40, 1), \
        font("HelveticaNeue.ttc", 36), font("HelveticaNeue.ttc", 30)
    d.text((80, 70), "Lunch menu, week 47", font=h1, fill=(20, 20, 20))
    d.text((80, 160), "Kirkkojärvi primary school, Espoo", font=body, fill=(90, 90, 90))
    days = [("Monday", "Meatball sauce, potatoes, lingonberry jam"), ("Tuesday", "Salmon soup, rye bread"),
            ("Wednesday", "Chicken curry, rice, cucumber salad"), ("Thursday", "Pea soup, pancakes with berry jam"),
            ("Friday", "Vegetable lasagne, green salad")]
    y = 270
    for day, dish in days:
        d.text((80, y), day, font=h2, fill=(20, 90, 50))
        d.text((80, y + 56), dish, font=body, fill=(30, 30, 30))
        d.text((80, y + 104), "Milk-free and gluten-free options available", font=small, fill=(110, 110, 110))
        y += 210
    d.line((80, 1380, 1160, 1380), fill=(200, 200, 200), width=2)
    d.text((80, 1410), "420 lunches / day · Nut-free school · Finnish ingredients first", font=small, fill=(60, 60, 60))
    d.text((80, 1460), "Summer break 1 Jun – 9 Aug", font=small, fill=(60, 60, 60))
    photo(img, 1.8).save(OUT / "menu-w47.jpg", quality=85)


def allergy_note() -> None:
    img = paper(1000, 1000, tint=(255, 241, 140))
    d = ImageDraw.Draw(img)
    big, hand = font("MarkerFelt.ttc", 96), font("Bradley Hand Bold.ttf", 58)
    d.text((70, 80), "NO NUTS", font=big, fill=(190, 20, 20))
    d.text((70, 210), "– not even 'may contain'!!", font=hand, fill=(190, 20, 20))
    d.text((70, 400), "18 milk-free diets", font=hand, fill=(20, 20, 20))
    d.text((70, 470), "→", font=font("Arial Unicode.ttf", 58), fill=(20, 20, 20))
    d.text((140, 480), "oat drink", font=hand, fill=(20, 20, 20))
    d.text((70, 620), "9 gluten-free", font=hand, fill=(20, 20, 20))
    d.text((70, 820), "Kitchen, 1 Sep", font=font("Bradley Hand Bold.ttf", 44), fill=(80, 80, 80))
    photo(img, 4).save(OUT / "allergy-note.jpg", quality=85)


INVOICES = [  # (date, total)
    ("2025-09-08", 512.40), ("2025-09-15", 548.90), ("2025-09-22", 530.10), ("2025-09-29", 561.75),
    ("2025-10-06", 525.30), ("2025-10-13", 553.20), ("2025-10-20", 539.85), ("2025-10-27", 548.50)]
LINES = [("463038", "Valio Eila® kevytmaitojuoma 10 l novobox", 48, 10.95),
         ("464411", "Valio voi suolaton 500 g", 80, 3.89),
         ("469786", "Vaasan Ruispalat 660g 12 pcs", 200, 2.79),
         ("460970", "Start Naturell granola 750g", 160, 3.15),
         ("470961", "PP pre-washed potato 35-50mm 15kg/bx", 40, 11.90)]


def invoices() -> None:
    pages = []
    h1, body, small = font("HelveticaNeue.ttc", 44, 1), font("HelveticaNeue.ttc", 26), font("HelveticaNeue.ttc", 22)
    for n, (day, total) in enumerate(INVOICES):
        img = Image.new("RGB", (1240, 1754), "white")
        d = ImageDraw.Draw(img)
        d.text((80, 80), "Valio Aimo Oy · Invoice", font=h1, fill=(20, 20, 20))
        d.text((80, 150), f"Invoice no. 90{4211 + n}   Delivery {day}   Customer: Kirkkojärvi school kitchen, Espoo",
               font=small, fill=(80, 80, 80))
        y = 240
        d.text((80, y), "Product", font=body, fill=(20, 20, 20))
        d.text((860, y), "Qty", font=body, fill=(20, 20, 20))
        d.text((1000, y), "EUR", font=body, fill=(20, 20, 20))
        y += 50
        sub = 0.0
        for pid, name, qty, price in LINES:
            q = max(1, round(qty / 8))
            d.text((80, y), f"{pid}  {name}", font=body, fill=(30, 30, 30))
            d.text((860, y), f"{q}", font=body, fill=(30, 30, 30))
            d.text((1000, y), f"{q * price:,.2f}", font=body, fill=(30, 30, 30))
            sub += q * price
            y += 46
        d.text((80, y), "Other products (12 lines)", font=body, fill=(30, 30, 30))
        d.text((1000, y), f"{total - sub:,.2f}", font=body, fill=(30, 30, 30))
        y += 46
        d.line((80, y + 10, 1160, y + 10), fill=(0, 0, 0), width=2)
        d.text((80, y + 30), "Total, net of VAT", font=body, fill=(20, 20, 20))
        d.text((1000, y + 30), f"{total:,.2f}", font=body, fill=(20, 20, 20))
        if n == len(INVOICES) - 1:
            d.text((80, y + 140), "Summary Sep–Oct, 8 deliveries:", font=body, fill=(20, 20, 20))
            y2 = y + 190
            for pid, name, qty, _ in LINES:
                d.text((80, y2), f"{name} ×{qty}", font=body, fill=(30, 30, 30))
                y2 += 42
            d.text((80, y2 + 20), "avg. € 540 per delivery", font=body, fill=(20, 20, 20))
        pages.append(img)
    pages[0].save(OUT / "invoices-oct.pdf", save_all=True, append_images=pages[1:], resolution=150)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    order_list()
    menu()
    allergy_note()
    invoices()
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)
