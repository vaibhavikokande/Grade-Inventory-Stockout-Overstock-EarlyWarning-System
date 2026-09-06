"""Rule-based product categorisation.

The source dataset (Online Retail II) has NO category column. Category is one
of the fields the early-warning system needs in order to answer "which
categories carry the most inventory risk", so it is DERIVED from the free-text
product description using an ordered keyword ruleset.

This is a documented derivation, not source data. Two consequences are stated
explicitly in the README and the data dictionary:

1. Rules are evaluated in priority order and the first match wins. Order
   matters: "JUMBO BAG RED RETROSPOT" must resolve to Bags, not Vintage &
   Retro, so Bags is evaluated first.
2. Anything unmatched falls to "Other". The share of SKUs and revenue landing
   in "Other" is reported as a quality metric, and a random sample is manually
   audited in notebooks/01_data_cleaning.ipynb.
"""

from __future__ import annotations

import re
from typing import Iterable

import pandas as pd

# Exclusion phrases. If a description contains one of these, the named category
# is skipped even when its keywords match. These were added after auditing a
# random sample of assignments: "BLUE GLASS GEMS IN BAG" is packaged in a bag
# but is not a bag, and "LOVEBIRD HANGING DECORATION" is not a garden product.
CATEGORY_EXCLUSIONS: dict[str, list[str]] = {
    "Bags": ["IN BAG", "BAG CHARM", "BAG OF"],
    "Garden & Outdoor": ["LOVEBIRD", "BIRD ORNAMENT", "BIRD DECORATION", "BIRD MOBILE"],
    # A soap DISH is bathroom homeware: neither a fragrance product nor
    # tableware, so both earlier categories must decline it.
    "Candles & Fragrance": ["SOAP DISH"],
    "Kitchen & Dining": ["SOAP DISH"],
    # "FRENCH"/"REGENCY" are styling adjectives on otherwise ordinary
    # homeware; they should not outrank a concrete product noun.
    "Vintage & Retro": ["BASIN", "BOWL", "PLATE", "CABINET", "TRAY"],
}

# Ordered (category, keywords) rules. FIRST MATCH WINS, so the most specific
# and least ambiguous categories are listed first and the weak catch-all
# categories (Vintage & Retro, Home Decor) are listed last.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Christmas & Seasonal", [
        "CHRISTMAS", "XMAS", "ADVENT", "SANTA", "REINDEER", "SNOWMAN", "SNOWFLAKE",
        "EASTER", "HALLOWEEN", "VALENTINE", "NATIVITY", "MISTLETOE", "TINSEL",
        "BAUBLE", "WREATH", "STOCKING", "NOEL", "ELF",
    ]),
    ("Candles & Fragrance", [
        "CANDLE", "T-LIGHT", "TLIGHT", "TEA LIGHT", "TEALIGHT", "INCENSE",
        "FRAGRANCE", "SCENT", "DIFFUSER", "POT POURRI", "POTPOURRI", "SOAP",
        "PERFUME", "AROMA",
    ]),
    ("Bags", [
        "BAG", "WASHBAG", "HANDBAG", "GIFTBAG", "TOTE", "RUCKSACK",
        "SATCHEL", "SHOPPER", "HOLDALL", "SACK",
    ]),
    ("Party & Celebration", [
        "PARTY", "BUNTING", "BALLOON", "CONFETTI", "GIFT WRAP", "WRAP",
        "RIBBON", "GIFT TAG", "GREETING CARD", "INVITE", "INVITATION",
        "WRAPPING",
        "GARLAND", "FIREWORK", "CELEBRATION", "CAKE CASE", "PAPER CHAIN",
        "BIRTHDAY", "WEDDING",
    ]),
    ("Toys & Games", [
        "TOY", "GAME", "PUZZLE", "JIGSAW", "DOLL", "TEDDY", "PLAYING CARD",
        "SKITTLE", "SPINNING TOP", "YOYO", "YO-YO", "SOLDIER", "BINGO",
        "DOMINOES", "SNAKES & LADDERS", "CHESS", "DRAUGHT", "KITE", "SLINKY",
        "MAGIC", "BAGATELLE", "SPACEBOY", "PLAYHOUSE", "ROBOT", "DINOSAUR",
    ]),
    ("Kitchen & Dining", [
        "MUG", "CUP", "PLATE", "BOWL", "TEAPOT", "JUG", "CAKE", "BAKING",
        "CUTLERY", "SPOON", "FORK", "KNIFE", "GLASS", "BOTTLE", "KITCHEN",
        "APRON", "TRAY", "COASTER", "NAPKIN", "TABLECLOTH", "CAKESTAND",
        "JAM ", "TEA ", "COFFEE", "PANTRY", "MEASURING", "WHISK", "ROLLING PIN",
        "BREAD BIN", "CHOPPING", "CAKE TIN", "BISCUIT", "MILK", "SUGAR",
        "EGG CUP", "EGG WARMER", "COOK", "OVEN", "TOAST", "DINNER", "PLACEMAT",
        "TUMBLER", "FLASK", "CERAMIC", "STRAW", "JELLY MOULD",
        "CAFETIERE", "CHOCOLATE", "SWEET", "CANDY", "RECIPE", "SERVING",
        "PAN", "SAUCEPAN", "FRYING", "COLANDER", "TEASPOON", "SCALES",
        "CROCKERY", "DISH", "PLATTER", "CANISTER", "TIN", "MOULD",
        "ENAMEL", "BASIN", "PITCHER", "CAROUSEL", "BAKEWARE",
    ]),
    ("Garden & Outdoor", [
        "GARDEN", "PLANT", "FLOWERPOT", "FLOWER POT", "SEED", "WATERING",
        "BIRD FEEDER", "BIRD BATH", "BIRD HOUSE", "BIRD TABLE", "BIRD SEED",
        "GNOME", "TROWEL", "GREENHOUSE", "POTTING", "HERB", "WINDMILL",
        "PARASOL", "DECKCHAIR", "PICNIC", "BUCKET", "SPADE", "WATER CAN",
        "OUTDOOR", "BEACH", "ALLOTMENT",
    ]),
    ("Stationery & Craft", [
        "NOTEBOOK", "PENCIL", "PEN ", "ERASER", "RUBBER", "CRAYON", "CHALK",
        "STICKER", "PAPER", "CRAFT", "SKETCHBOOK", "JOURNAL", "DIARY",
        "ENVELOPE", "NOTEPAD", "RULER", "SCISSOR", "GLUE", "STAMP", "SEWING",
        "KNIT", "KNITTED", "FELT", "BEADS KIT", "COLOURING", "STENCIL",
        "NOTE PAD", "WRITING", "GIFT BOX",
    ]),
    ("Jewellery & Accessories", [
        "NECKLACE", "BRACELET", "EARRING", "BROOCH", "PENDANT", "BEAD",
        "SCARF", "HAT", "GLOVE", "UMBRELLA", "SLIPPER", "SOCK", "JEWELLERY",
        "CHARM", "HAIR", "WATCH", "SUNGLASS", "PURSE", "WALLET", "APRON",
        "TIARA", "CUFFLINK", "RING ",
    ]),
    ("Lighting", [
        "LIGHT", "LAMP", "LANTERN", "TORCH", "CHANDELIER", "LED", "BULB",
        "FAIRY LIGHT", "NIGHTLIGHT",
    ]),
    ("Storage & Organisation", [
        "STORAGE", "ORGANISER", "CADDY", "DRAWER", "CRATE", "BASKET", "SHELF",
        "RACK", "HANGER", "TIDY", "CHEST", "BOX", "JAR", "HOOK", "TRUNK",
        "SUITCASE", "CABINET", "HOLDER", "STAND", "CONTAINER",
    ]),
    ("Home Decor", [
        "HEART", "ORNAMENT", "FRAME", "MIRROR", "CUSHION", "DOORMAT", "CLOCK",
        "SIGN", "HANGING", "DECORATION", "VASE", "WALL", "PHOTO", "DOORSTOP",
        "BLACKBOARD", "BLACK BOARD", "CHALKBOARD", "CHIME", "CANVAS", "PLAQUE",
        "CURTAIN", "RUG", "THROW", "DRAWER KNOB", "KNOB", "HOME", "DECORATIVE",
        "FLOWER", "LETTERS", "BIRD", "MOBILE", "CAGE", "MAGNET",
        "SOAP DISH", "BUTTERFLY", "STAR", "BIRDCAGE",
    ]),
    ("Vintage & Retro", [
        "VINTAGE", "RETRO", "RETROSPOT", "ANTIQUE", "NOSTALGI", "50'S",
        "1950", "REGENCY", "VICTORIAN", "FRENCH", "DOILY", "POLKADOT",
        "POLKA DOT",
    ]),
]

OTHER = "Other"
CATEGORIES: list[str] = [name for name, _ in CATEGORY_RULES] + [OTHER]

def _alternation(words: list[str]) -> re.Pattern[str]:
    """Compile keywords into one word-boundary-anchored alternation.

    Anchoring matters: an unanchored "PEN" matched "O(PEN S)HAPE" and filed a
    decorative ornament under Stationery. Each keyword also accepts a regular
    plural so "CANDLE" covers "CANDLES" without matching "CANDLESTICK".
    """
    parts = [rf"\b{re.escape(w.strip())}(?:S|ES)?\b" for w in words]
    return re.compile("|".join(parts))


# Pre-compile one alternation regex per category for speed over ~5k SKUs.
_COMPILED: list[tuple[str, re.Pattern[str], re.Pattern[str] | None]] = [
    (name, _alternation(keywords),
     _alternation(CATEGORY_EXCLUSIONS[name]) if name in CATEGORY_EXCLUSIONS else None)
    for name, keywords in CATEGORY_RULES
]


def categorise_description(description: str | float | None) -> str:
    """Map a single product description to a category (first rule wins).

    A category is skipped when one of its exclusion phrases is present, which
    lets a later, more appropriate rule claim the product.
    """
    if not isinstance(description, str) or not description.strip():
        return OTHER
    text = description.upper()
    for name, pattern, exclusions in _COMPILED:
        if exclusions is not None and exclusions.search(text):
            continue
        if pattern.search(text):
            return name
    return OTHER


def categorise(descriptions: Iterable[str]) -> pd.Series:
    """Vectorised wrapper returning a categorical Series."""
    series = pd.Series(list(descriptions), dtype="object")
    return series.map(categorise_description).astype(
        pd.CategoricalDtype(categories=CATEGORIES)
    )


def coverage_report(product_dim: pd.DataFrame,
                    revenue_col: str = "total_revenue") -> pd.DataFrame:
    """Summarise how much of the catalogue and revenue each category captures.

    Used to justify the derivation: a large "Other" bucket would mean the
    ruleset is too weak to support category-level conclusions.
    """
    grouped = product_dim.groupby("category", observed=False).agg(
        n_skus=("product_id", "size"),
        revenue=(revenue_col, "sum"),
    )
    grouped["pct_skus"] = (100 * grouped.n_skus / grouped.n_skus.sum()).round(2)
    grouped["pct_revenue"] = (100 * grouped.revenue / grouped.revenue.sum()).round(2)
    return grouped.sort_values("revenue", ascending=False)
