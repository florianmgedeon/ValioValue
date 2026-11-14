"""The tricky cases from the accuracy spec. Must pass on every change."""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from valio_mcp.catalog import Product
from valio_mcp.constraints import evaluate, milk_scan


def prod(name="x", contains=(), may=(), free=(), claims=(), ingredients=None, data=True, country="fi") -> Product:
    return Product(id="T", name=name, names_all=name.lower(), name_en=name.lower(), brand=None, vendor=None,
                   category="C", sales_unit="PAK", base_unit="PAK", lot_size=1, country=country,
                   allergens_contains=list(contains), allergens_may_contain=list(may),
                   allergens_free_from=list(free), claims=list(claims), key_ingredients=ingredients,
                   has_allergen_data=data or bool(contains or may or free))


MILK_ALLERGY = {"rule": "exclude_allergen", "allergen": "milk", "strictness": "contains_or_may_contain"}
LACTOSE_FREE = {"rule": "lactose_free"}

CASES = [
    # (case, product, rule, expected verdict)
    ("lactose-free milk under milk allergy", prod("Laktoositon maitojuoma", ["milk"], free=["lactose"]), MILK_ALLERGY, "excluded"),
    ("lactose-free milk under lactose intolerance", prod("Laktoositon maitojuoma", ["milk"], free=["lactose"]), LACTOSE_FREE, "safe"),
    ("oat drink is not milk", prod("Oat milk drink", ingredients="water, oats, rapeseed oil"), MILK_ALLERGY, "safe"),
    ("coconut milk is not milk", prod("Kookosmaito 400 ml"), MILK_ALLERGY, "safe"),
    ("cocoa butter is not butter", prod("Dark bar", ingredients="cocoa mass, sugar, cocoa butter"), MILK_ALLERGY, "safe"),
    ("peanut butter is not butter", prod("Peanut butter"), MILK_ALLERGY, "safe"),
    ("dark chocolate may contain milk, allergy", prod("Dark chocolate 70%", may=["milk"]), MILK_ALLERGY, "excluded"),
    ("dark chocolate may contain milk, contains-only", prod("Dark chocolate 70%", may=["milk"]),
     dict(MILK_ALLERGY, strictness="contains"), "safe"),
    ("bread with sodium caseinate", prod("Toast bread", ingredients="wheat flour, water, sodium caseinate"), MILK_ALLERGY, "excluded"),
    ("non-dairy creamer with casein: claim loses", prod("Non-dairy creamer", claims=["FREE_FROM_MILK"],
                                                        ingredients="glucose syrup, palm oil, casein"), MILK_ALLERGY, "excluded"),
    ("no allergen data is unknown", prod("Mystery spread", data=False), MILK_ALLERGY, "unknown"),
    ("no allergen data is unknown for lactose", prod("Mystery spread", data=False), LACTOSE_FREE, "unknown"),
    ("plain milk is not lactose-free", prod("Kevytmaito 1 l", ["milk"]), LACTOSE_FREE, "excluded"),
    ("low lactose is not lactose-free", prod("Hyla", ["milk"], claims=["LOW_LACTOSE"]), LACTOSE_FREE, "excluded"),
    ("maidoton means milk-free", prod("Maidoton levite", ingredients="kasviöljy, vesi"), MILK_ALLERGY, "safe"),
    ("origin missing is unknown", prod("Apple", country=None), {"rule": "origin", "country": "fi"}, "unknown"),
]


@pytest.mark.parametrize("case,p,rule,expected", CASES, ids=[c[0] for c in CASES])
def test_tricky_cases(case, p, rule, expected):
    ev = evaluate(p, rule)
    assert ev["verdict"] == expected, f"{case}: {ev}"
    if expected != "unknown":
        assert ev["signals"], "every decided verdict carries evidence"


PLANT = ["coconut milk", "oat milk", "soy milk", "almond milk", "cocoa butter", "peanut butter", "kaurajuoma",
         "kookosmaito", "kaakaovoi", "maidoton", "milk-free", "dairy free"]
MILK = ["milk", "cream", "butter", "whey", "casein", "sodium caseinate", "ghee", "kevytmaito", "kerma", "juusto",
        "herajauhe", "laktoositon maitojuoma"]
FILLER = ["water", "sugar", "salt", "wheat flour", "rapeseed oil", "vesi", "sokeri", "suola"]


@settings(max_examples=300)
@given(st.lists(st.sampled_from(PLANT + FILLER), min_size=1, max_size=6))
def test_plant_lookalikes_never_flag_milk(words):
    assert milk_scan(", ".join(words)) == []


@settings(max_examples=300)
@given(st.lists(st.sampled_from(PLANT + FILLER), max_size=5), st.sampled_from(MILK), st.integers(0, 5))
def test_any_milk_word_is_caught(words, milk, pos):
    words.insert(min(pos, len(words)), milk)
    assert milk_scan(", ".join(words)) != []


ALLERGENS = ["milk", "lactose", "gluten", "egg", "peanuts", "soy"]


@settings(max_examples=500)
@given(contains=st.sets(st.sampled_from(ALLERGENS)), may=st.sets(st.sampled_from(ALLERGENS)),
       free=st.sets(st.sampled_from(ALLERGENS)), data=st.booleans(),
       target=st.sampled_from(ALLERGENS[:1] + ALLERGENS[2:]),
       strict=st.sampled_from(["contains", "contains_or_may_contain"]))
def test_allergen_property(contains, may, free, data, target, strict):
    """Blanket property: a product with any 'contains' signal for the target is never safe,
    and strict mode never lets 'may contain' through."""
    p = prod("Generic product", contains, may, free, data=data)
    ev = evaluate(p, {"rule": "exclude_allergen", "allergen": target, "strictness": strict})
    related = {target} | ({"lactose"} if target == "milk" else set())
    if related & contains:
        assert ev["verdict"] == "excluded"
    if strict == "contains_or_may_contain" and related & may:
        assert ev["verdict"] == "excluded"
    if ev["verdict"] == "safe":
        assert not (related & contains)
        assert any(s["says"] == "safe" for s in ev["signals"])
