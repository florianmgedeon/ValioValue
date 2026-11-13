"""Static product catalog: loading, normalising and local search.

The Valio mock API has no search endpoint and must not be used to price the
whole catalog, so all narrowing happens here on the static product file.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("valio_mcp.catalog")

# GS1 allergen type codes (subset) -> readable name.
GS1_ALLERGENS = {
    "AM": "milk", "AE": "egg", "AF": "fish", "AN": "tree nuts", "AP": "peanuts",
    "AS": "sesame", "AU": "sulphites", "AW": "gluten", "AY": "soy", "AC": "crustaceans",
    "UM": "molluscs", "BC": "celery", "BM": "mustard", "NL": "lupin", "ML": "lactose",
    "GB": "barley", "GK": "kamut", "GO": "oats", "GS": "spelt", "NR": "rye", "UW": "wheat",
}

# Search vocabulary. Catalog names are mostly Finnish, agents mostly speak English.
SYNONYMS: dict[str, list[str]] = {
    "milk": ["maito", "mjölk", "milk"],
    "lactose-free": ["laktoositon", "laktosfri", "lactose free", "lactose-free"],
    "lactose free": ["laktoositon", "laktosfri", "lactose free", "lactose-free"],
    "cream": ["kerma", "grädde", "cream"],
    "butter": ["voi", "smör", "butter"],
    "cheese": ["juusto", "ost", "cheese"],
    "yoghurt": ["jogurtti", "yoghurt", "yogurt"],
    "yogurt": ["jogurtti", "yoghurt", "yogurt"],
    "oat": ["kaura", "havre", "oat"],
    "egg": ["kananmuna", "muna", "ägg", "egg"],
    "potato": ["peruna", "potatis", "potato"],
    "chicken": ["broileri", "kana", "kyckling", "chicken"],
}

LACTOSE_FREE_WORDS = ("laktoositon", "laktosfri", "lactose free", "lactose-free")
# Words that only describe filters, not the product itself.
STOPWORDS = {"i", "need", "a", "an", "the", "of", "by", "for", "litres", "liters", "litre", "liter",
             "l", "kg", "kilos", "some", "and", "with", "free", "lactose", "lactose-free"}


def _text(items: list[dict] | None, prefer: tuple[str, ...] = ("en", "fi", "sv")) -> str | None:
    if not items:
        return None
    by_lang = {i.get("language"): i.get("value") for i in items if i.get("value")}
    for lang in prefer:
        if by_lang.get(lang):
            return by_lang[lang]
    return next(iter(by_lang.values()), None)


@dataclass
class Product:
    id: str
    name: str
    names_all: str
    brand: str | None
    vendor: str | None
    category: str
    sales_unit: str
    base_unit: str
    lot_size: float
    country: str | None
    allergens_contains: list[str] = field(default_factory=list)
    allergens_free_from: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    lactose_free: bool = False
    substitutes: list[str] = field(default_factory=list)
    key_ingredients: str | None = None
    delivery_reliability: float | None = None
    co2_kg_per_kg: float | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "product_id": self.id,
            "name": self.name,
            "brand": self.brand,
            "vendor": self.vendor,
            "category": self.category,
            "sales_unit": self.sales_unit,
            "allowed_lot_size": self.lot_size,
            "country_of_origin": self.country,
            "lactose_free": self.lactose_free,
            "allergens": self.allergens_contains,
            "free_from": self.allergens_free_from,
            "certificates": self.labels,
            "claims": self.claims,
            "delivery_reliability": self.delivery_reliability,
            "co2_kg_per_kg": self.co2_kg_per_kg,
            "has_substitutes": bool(self.substitutes),
        }


def _parse(raw: dict) -> Product:
    sd = raw.get("synkkaData") or {}
    names = sd.get("names") or []
    name = _text(names) or raw["id"]
    contains, free_from, labels, claims = [], [], [], []
    for group in sd.get("classifications") or []:
        gname = group.get("name")
        for v in group.get("values") or []:
            code = v.get("synkkaId") or v.get("id") or ""
            if gname == "allergen":
                readable = GS1_ALLERGENS.get(code.upper(), code.lower())
                (free_from if v.get("unit") == "FREE_FROM" else contains).append(readable)
            elif gname == "nonAllergen" and v.get("unit") == "FREE_FROM":
                free_from.append(GS1_ALLERGENS.get(code.upper(), code.lower()))
            elif gname == "packagingMarkedLabel":
                labels.append(code)
            elif gname == "nutritionalClaim":
                claims.append(code)
    names_all = " | ".join(n.get("value", "") for n in names).lower()
    lactose_free = (
        any(w in names_all for w in LACTOSE_FREE_WORDS)
        or any("LACTOSE" in c.upper() for c in claims)
        or "lactose" in free_from
    )
    return Product(
        id=str(raw["id"]),
        name=name,
        names_all=names_all,
        brand=sd.get("brand"),
        vendor=raw.get("vendorName"),
        category=str(raw.get("category", "")),
        sales_unit=raw.get("salesUnit", ""),
        base_unit=raw.get("baseUnit", ""),
        lot_size=float(raw.get("allowedLotSize") or 1),
        country=raw.get("countryOfOrigin"),
        allergens_contains=sorted(set(contains)),
        allergens_free_from=sorted(set(free_from)),
        labels=sorted(set(labels)),
        claims=sorted(set(claims)),
        lactose_free=lactose_free,
        substitutes=[s["substituteProduct"] for s in raw.get("substitutions") or [] if s.get("substituteProduct")],
        key_ingredients=_text(sd.get("keyIngredients")),
    )


def _load_csv(path: Path, value_col: str) -> dict[str, float]:
    if not path.exists():
        log.warning("missing %s", path)
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        out = {}
        for row in csv.DictReader(f):
            try:
                out[row["productId"]] = float(row[value_col])
            except (KeyError, ValueError):
                continue
        return out


class Catalog:
    def __init__(self, products_path: Path, data_dir: Path):
        raw = json.loads(products_path.read_text(encoding="utf-8"))
        reliability = _load_csv(data_dir / "delivery_reliability.csv", "deliveryReliability")
        co2 = _load_csv(data_dir / "carbon_footprint.csv", "AVG kg CO2-eq/kg")
        self.products: dict[str, Product] = {}
        for item in raw:
            if item.get("deleted"):
                continue
            p = _parse(item)
            p.delivery_reliability = reliability.get(p.id)
            p.co2_kg_per_kg = co2.get(p.id)
            self.products[p.id] = p
        self.source = products_path.name
        log.info("catalog loaded: %d products from %s", len(self.products), products_path)

    def get(self, product_id: str) -> Product | None:
        return self.products.get(str(product_id))

    @staticmethod
    def _terms(query: str) -> list[list[str]]:
        """Each term becomes a list of alternatives (synonyms in fi/sv/en)."""
        q = query.lower().strip()
        terms: list[list[str]] = []
        for phrase, alts in SYNONYMS.items():
            if " " in phrase and phrase in q:
                q = q.replace(phrase, " ")
        for tok in re.findall(r"[\wäöå-]+", q):
            if tok in STOPWORDS or tok.isdigit():
                continue
            terms.append(SYNONYMS.get(tok, [tok]))
        return terms

    def search(
        self,
        query: str,
        *,
        lactose_free: bool | None = None,
        exclude_allergens: list[str] | None = None,
        category: str | None = None,
        country_of_origin: str | None = None,
        limit: int = 10,
    ) -> tuple[list[Product], int]:
        terms = self._terms(query)
        if "lactose" in query.lower() and ("free" in query.lower() or "laktoositon" in query.lower()):
            lactose_free = True if lactose_free is None else lactose_free
        exclude = {a.lower() for a in exclude_allergens or []}
        scored: list[tuple[float, Product]] = []
        for p in self.products.values():
            if lactose_free and not p.lactose_free:
                continue
            if category and p.category != category:
                continue
            if country_of_origin and (p.country or "").lower() != country_of_origin.lower():
                continue
            if exclude and exclude & set(p.allergens_contains):
                continue
            hay = f"{p.names_all} {(p.brand or '').lower()}"
            score = 0.0
            for alts in terms:
                best = 0.0
                for a in alts:
                    if re.search(rf"\b{re.escape(a)}\b", hay):
                        best = max(best, 2.0)
                    elif a in hay:
                        best = max(best, 1.0)
                if best == 0.0:
                    score = -1
                    break
                score += best
            if terms and score <= 0:
                continue
            if p.delivery_reliability is not None:
                score += p.delivery_reliability * 0.5
            scored.append((score, p))
        scored.sort(key=lambda t: (-t[0], t[1].name))
        return [p for _, p in scored[:limit]], len(scored)


_catalog: Catalog | None = None


def catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog(config.products_file(), config.DATA_DIR)
    return _catalog
