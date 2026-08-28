from __future__ import annotations

from collections import defaultdict

from .models import Excerpt, ImportantFinding, Importance


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Namenska raba in gradnja": (
        "namenska raba",
        "stavbno zemljišče",
        "gradnja",
        "dopustn",
        "objekt",
        "land use",
        "construction",
    ),
    "Omejitve": (
        "prepoved",
        "omejitev",
        "ni dovoljeno",
        "varovalni pas",
        "režim",
        "restriction",
        "prohibited",
    ),
    "Poplave in vode": (
        "poplav",
        "vodno zemljišče",
        "priobal",
        "erozij",
        "plaz",
        "flood",
        "watercourse",
    ),
    "Dediščina in narava": (
        "kulturna dediščina",
        "naravna vrednota",
        "natura 2000",
        "spomenik",
        "heritage",
        "protected area",
    ),
    "Komunalna infrastruktura": (
        "komunal",
        "kanaliz",
        "vodovod",
        "elektro",
        "plinovod",
        "infrastruk",
        "utility",
        "corridor",
    ),
    "Ceste in dostop": (
        "cesta",
        "dostop",
        "dovoz",
        "parkir",
        "promet",
        "road",
        "access",
    ),
    "Obveznosti lastnika": (
        "investitor mora",
        "lastnik mora",
        "obveznost",
        "dolžan",
        "owner must",
        "obligation",
    ),
    "Okoljske zahteve": (
        "okolj",
        "hrup",
        "zrak",
        "odpad",
        "omilitven",
        "environment",
        "noise",
    ),
}


HIGH_RISK_CATEGORIES = {"Poplave in vode", "Dediščina in narava", "Omejitve"}


def extract_findings(excerpts: list[Excerpt]) -> list[ImportantFinding]:
    pages: dict[str, set[int]] = defaultdict(set)
    evidence: dict[str, str] = {}
    for excerpt in excerpts:
        lowered = excerpt.text.casefold()
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                pages[category].add(excerpt.page)
                evidence.setdefault(category, excerpt.text[:360].rstrip(" …"))
    return [
        ImportantFinding(
            category=category,
            detail=(
                "V odlomkih, povezanih s parcelo, je zaznana vsebina s tega področja. "
                "Za natančen pogoj preverite navedene strani in celotno besedilo: "
                + evidence[category]
            ),
            importance=(
                Importance.high
                if category in HIGH_RISK_CATEGORIES
                else Importance.medium
            ),
            pages=sorted(category_pages),
        )
        for category, category_pages in pages.items()
    ]


def extractive_summary(
    parcel_number: str, excerpts: list[Excerpt], mention_count: int
) -> str:
    if mention_count == 0:
        return (
            f"Parcela {parcel_number} je bila s prostorskim aktom povezana prek prostorskega preseka PIS. "
            "Za presojo uporabite namensko rabo, EUP/PEUP, grafični izris in uradno besedilo akta; "
            "neposreden zapis parcelne številke v posameznem PDF-ju ni pogoj za prostorsko povezavo."
        )
    sections = sorted({excerpt.section for excerpt in excerpts if excerpt.section})
    section_text = (
        f" Zadevni razdelki: {', '.join(sections[:6])}." if sections else ""
    )
    return (
        f"Parcela {parcel_number} se v besedilu PDF-ja neposredno pojavlja {mention_count}-krat na "
        f"{len({item.page for item in excerpts})} straneh.{section_text} "
        "Poudarjene ugotovitve so zaznane po ključnih besedah, spodnji odlomki pa ohranjajo izvorni kontekst."
    )
