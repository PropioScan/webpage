from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass

from .models import PlanningCondition, PlanningContext, PreemptionRightAssessment


@dataclass(frozen=True)
class PlanningTextSource:
    title: str
    url: str
    pages: list[str]


@dataclass(frozen=True)
class _Topic:
    key: str
    title: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class _KranjEUPProfile:
    planning_unit: str
    land_use: str
    max_fi: str
    max_fz: str
    min_open_space: str
    max_floors: str
    urban_form: str
    regulation_method: str
    special_provision: str
    source: PlanningTextSource
    page_number: int


TOPICS: tuple[_Topic, ...] = (
    _Topic(
        "activities",
        "Vrste dopustnih dejavnosti",
        (
            "vrste dopustnih dejavnosti",
            "dopustne dejavnosti",
            "dopustne namembnosti",
            "namembnosti oziroma dejavnosti",
        ),
    ),
    _Topic(
        "construction",
        "Vrste dopustnih gradenj in drugih del",
        (
            "vrste dopustnih gradenj in drugih del",
            "vrste dopustnih gradenj oziroma drugih del",
            "dopustne gradnje",
            "dopustni posegi",
        ),
    ),
    _Topic(
        "objects",
        "Vrste dopustnih objektov glede na namen",
        (
            "vrste dopustnih objektov glede na namen",
            "dopustni objekti in naprave",
            "dopustne vrste objektov",
            "vrste objektov",
        ),
    ),
    _Topic(
        "functional_design",
        "Osnovni funkcionalni in oblikovni pogoji",
        (
            "osnovni funkcionalni in oblikovni pogoji",
            "funkcionalna in oblikovna merila in pogoji",
            "funkcionalni in oblikovni pogoji",
            "splošni prostorski izvedbeni pogoji",
        ),
    ),
    _Topic(
        "development_type",
        "Tip in način zazidave",
        ("tip zazidave", "tipologija zazidave", "način zazidave"),
    ),
    _Topic(
        "size",
        "Velikost in gabariti objektov",
        (
            "velikost in gabariti objektov",
            "gabariti objektov",
            "gabariti objekta",
            "pogoji glede velikosti objektov",
            "velikost objektov",
        ),
    ),
    _Topic(
        "roof_facade",
        "Strehe, fasade in zunanja podoba",
        (
            "strehe in fasade",
            "strehe objektov",
            "oblikovanje fasad",
            "zunanja podoba objektov",
            "oblikovanje zunanje podobe",
        ),
    ),
    _Topic(
        "location_setbacks",
        "Lega objektov in odmiki",
        (
            "lega objektov in odmiki",
            "lega objekta",
            "odmiki objektov",
            "odmiki od parcelnih mej",
            "odmiki od meja",
        ),
    ),
    _Topic(
        "surroundings",
        "Ureditev okolice in zelene površine",
        (
            "ureditev okolice in zelene površine",
            "ureditev okolice objektov",
            "ureditev okolice",
            "zelene površine",
        ),
    ),
    _Topic(
        "utilization",
        "Stopnja izkoriščenosti zemljišča",
        (
            "stopnja izkoriščenosti zemljišča",
            "stopnja izkoriščenosti",
            "dopustna izraba prostora",
            "faktor zazidanosti",
            "faktor izrabe",
        ),
    ),
    _Topic(
        "building_plot",
        "Velikost in oblika gradbene parcele",
        (
            "velikost in oblika gradbene parcele",
            "velikost in oblika parcele",
            "merila za parcelacijo",
            "parcelacija stavbnih zemljišč",
        ),
    ),
    _Topic(
        "road_parking",
        "Dostop do javne ceste, promet in parkiranje",
        (
            "priključevanje na javno cesto",
            "dostop do javne ceste",
            "prometno omrežje",
            "parkirne površine",
            "parkirna mesta",
        ),
    ),
    _Topic(
        "utilities",
        "Komunalna, energetska in komunikacijska infrastruktura",
        (
            "komunalna in energetska infrastruktura",
            "gospodarsko javno infrastrukturo",
            "komunalno opremljanje",
            "kanalizacijsko omrežje",
            "vodovodno omrežje",
        ),
    ),
    _Topic(
        "heritage_nature",
        "Varstvo kulturne dediščine in ohranjanje narave",
        (
            "varstvo kulturne dediščine in ohranjanje narave",
            "varstvo kulturne dediščine",
            "ohranjanje narave",
            "varstvo narave",
        ),
    ),
    _Topic(
        "environment",
        "Varstvo okolja in naravnih dobrin",
        (
            "varstvo okolja in naravnih dobrin",
            "varstvo okolja",
            "varstvo zraka",
            "varstvo tal",
            "varstvo voda",
        ),
    ),
    _Topic(
        "hazards",
        "Varstvo pred naravnimi in drugimi nesrečami",
        (
            "varstvo pred naravnimi in drugimi nesrečami",
            "naravne in druge nesreče",
            "poplavna območja",
            "erozijska območja",
            "varstvo pred požarom",
        ),
    ),
    _Topic(
        "health",
        "Varovanje zdravja ljudi",
        (
            "varovanje zdravja ljudi",
            "varstvo pred hrupom",
            "elektromagnetno sevanje",
            "svetlobno onesnaževanje",
            "zdravje ljudi",
        ),
    ),
)

_MISSING_DESCRIPTION = (
    "V pregledanem besedilu prostorskega akta ta sklop ni bil samodejno "
    "izluščen. Pogoje je treba preveriti v celotnem odloku oziroma pri občini."
)

_PREEMPTION_LEGAL_BASIS = (
    "199.–201. člen Zakona o urejanju prostora (ZUreP-3) in veljavni občinski odlok"
)
_GORENJA_VAS_PREEMPTION_URL = (
    "https://www.uradni-list.si/glasilo-uradni-list-rs/vsebina/"
    "2021-01-1451/odlok-o-predkupni-pravici-obcine-gorenja-vas---poljane"
)
_BUILDING_LAND_CODES = frozenset(
    {
        "A", "B", "BC", "BD", "BT", "C", "CD", "CU", "E", "I", "IG", "IK",
        "IP", "O", "P", "PC", "PH", "PL", "PO", "PZ", "PŽ", "S", "SB", "SK",
        "SP", "SS", "T", "Z", "ZD", "ZK", "ZP", "ZS",
    }
)


def is_textual_planning_document(source_name: str) -> bool:
    normalized = _normalize(source_name)
    filename = source_name.rsplit("/", 1)[-1]
    return "tekstualni_del" in normalized or "odlok" in _normalize(filename)


def extract_planning_conditions(
    sources: list[PlanningTextSource], contexts: list[PlanningContext]
) -> list[PlanningCondition]:
    context_terms = _context_terms(contexts)
    municipal_overrides = _kranj_condition_overrides(sources, contexts)
    conditions: list[PlanningCondition] = []
    for topic in TOPICS:
        if topic.key in municipal_overrides:
            conditions.append(municipal_overrides[topic.key])
            continue
        candidate = _best_candidate(topic, sources, context_terms)
        if candidate is None:
            conditions.append(
                PlanningCondition(
                    key=topic.key,
                    title=topic.title,
                    description=_MISSING_DESCRIPTION,
                )
            )
            continue
        description, source, page_number = candidate
        conditions.append(
            PlanningCondition(
                key=topic.key,
                title=topic.title,
                description=description,
                available=True,
                source_title=source.title,
                source_url=source.url,
                pages=[page_number],
            )
        )
    return conditions


def _kranj_condition_overrides(
    sources: list[PlanningTextSource], contexts: list[PlanningContext]
) -> dict[str, PlanningCondition]:
    """Build contextual Kranj PIP results from the current official text and EUP table."""
    ordinance = next(
        (
            source
            for source in sources
            if "neuradno-precisceno-besedilo.pdf" in source.url.casefold()
        ),
        None,
    )
    annex = next(
        (
            source
            for source in sources
            if "preglednica_20eup.pdf" in source.url.casefold()
        ),
        None,
    )
    planning_unit = next(
        (context.planning_unit for context in contexts if context.planning_unit), None
    )
    land_use_code = next(
        (context.land_use_code for context in contexts if context.land_use_code), None
    )
    if ordinance is None or annex is None or not planning_unit or not land_use_code:
        return {}

    profile = _extract_kranj_eup_profile(annex, planning_unit)
    if profile is None:
        return {}

    overrides: dict[str, PlanningCondition] = {}

    land_use_section = _find_land_use_section(ordinance, land_use_code)
    if land_use_section:
        excerpt, page = land_use_section
        overrides["activities"] = _available_condition(
            "activities", excerpt, ordinance, [page]
        )
        overrides["objects"] = _available_condition(
            "objects", excerpt, ordinance, [page]
        )

    section_map = {
        "construction": ("2.1.2 Dopustne vrste gradenj",),
        "functional_design": (
            "2.4.1.1 Urbanistično oblikovanje - splošni pogoji",
        ),
        "surroundings": ("2.4.10 Pogoji za oblikovanje okolice objektov",),
        "building_plot": (
            "2.5.1 Pogoji za oblikovanje parcel, namenjenih gradnji",
        ),
        "environment": ("2.7.3.1 Splošni pogoji",),
        "hazards": ("2.7.5.1 Splošni pogoji",),
    }
    page_hints = {
        "construction": range(20, 28),
        "functional_design": range(27, 31),
        "surroundings": range(39, 43),
        "building_plot": range(40, 44),
        "environment": range(54, 59),
        "hazards": range(59, 64),
    }
    for key, aliases in section_map.items():
        candidate = _find_exact_section(
            ordinance, aliases, page_hints=page_hints.get(key)
        )
        if candidate:
            excerpt, page = candidate
            overrides[key] = _available_condition(key, excerpt, ordinance, [page])

    eup = profile.planning_unit
    overrides["development_type"] = _available_condition(
        "development_type",
        (
            f"Za EUP {eup} je določen način urbanističnega oblikovanja "
            f"»{profile.urban_form}«. Pri gručasti zazidavi je treba ohranjati "
            "tradicionalni vzorec stavb v gručah, obstoječe dominante, značilne "
            "poglede in avtohtono vegetacijo na robu naselja."
        ),
        annex,
        [profile.page_number],
    )
    overrides["size"] = _available_condition(
        "size",
        (
            f"Za EUP {eup} je največja etažnost {profile.max_floors}. "
            f"Največji faktor zazidanosti (FZ): {_display_fz(profile.max_fz)}. "
            "Tlorisni gabarit je treba določiti glede na namen stavbe, velikost "
            "parcele in dovoljeno stopnjo izkoriščenosti."
        ),
        annex,
        [profile.page_number],
    )
    overrides["utilization"] = _available_condition(
        "utilization",
        (
            f"Za EUP {eup}: največji FI {_display_optional(profile.max_fi)}, "
            f"največji FZ {_display_fz(profile.max_fz)}, najmanjši delež odprtih "
            f"bivalnih površin (OBP) {profile.min_open_space} in največja "
            f"etažnost {profile.max_floors}. Način urejanja: "
            f"{profile.regulation_method}."
        ),
        annex,
        [profile.page_number],
    )
    overrides["roof_facade"] = _available_condition(
        "roof_facade",
        (
            "Za namensko rabo SK so dovoljene hiše vaškega tipa, eno- in "
            "dvostanovanjske prostostoječe hiše, nestanovanjske kmetijske "
            "stavbe ter v odloku navedeni drugi stavbni tipi. Naklon in oblika "
            "strehe, kritina, fasada, gabarit ter materiali se določijo po "
            "izbranem stavbnem tipu v 17. členu; zasnova se mora prilagoditi "
            "lokalni tradiciji in okoliški pozidavi."
        ),
        ordinance,
        [30, 31, 32, 33, 34, 35, 36],
    )
    overrides["location_setbacks"] = _available_condition(
        "location_setbacks",
        (
            "Za rabo SK morajo biti nadzemni deli novih zahtevnih in manj "
            "zahtevnih stavb od meje parcele praviloma odmaknjeni najmanj 4,0 m. "
            "Pri manj zahtevnih objektih je s pisnim soglasjem sosedov dovoljen "
            "manjši odmik, vendar ne manj kot 2,5 m. Od javne ceste velja za "
            "stavbe praviloma najmanj 5,0 m, za nezahtevne in enostavne objekte "
            "pa 2,0 m; upoštevati je treba tudi gradbene linije in pogoje upravljavca."
        ),
        ordinance,
        [25, 26, 27],
    )
    overrides["road_parking"] = _available_condition(
        "road_parking",
        (
            "Parcela, namenjena gradnji, mora imeti zagotovljen dovoz in dostop "
            "do javne ceste. Cestni priključek in prometna ureditev morata "
            "izpolnjevati pogoje upravljavca ter zagotavljati varen promet. "
            "Potrebno število parkirnih mest se zagotovi na parceli glede na "
            "vrsto in obseg dejavnosti; urediti je treba tudi manipulativne in "
            "intervencijske površine."
        ),
        ordinance,
        [38, 42, 44],
    )
    overrides["utilities"] = _available_condition(
        "utilities",
        (
            "Objekte je treba priključiti na razpoložljivo gospodarsko javno "
            "infrastrukturo pod pogoji upravljavcev. Odlok ločeno ureja vodovod, "
            "odvajanje komunalnih in padavinskih voda, prometno infrastrukturo, "
            "električno energijo, plin ter komunikacijsko omrežje; če omrežje ni "
            "na voljo, so dopustne le rešitve, ki jih odlok in področni predpisi "
            "izrecno omogočajo."
        ),
        ordinance,
        [42, 43, 44, 46, 47, 48, 49],
    )
    overrides["heritage_nature"] = _available_condition(
        "heritage_nature",
        (
            "Na objektih in območjih kulturne dediščine veljajo varstveni režimi "
            "ter kulturnovarstveni pogoji in soglasje. Na zavarovanih območjih, "
            "naravnih vrednotah in območjih Natura 2000 je treba posege načrtovati "
            "tako, da se ohranjajo varovane lastnosti; kadar bi jih poseg lahko "
            "ogrozil, so potrebni naravovarstveni pogoji in soglasje. Dejanski "
            "presek parcele je prikazan v sklopu varovanj in omejitev."
        ),
        ordinance,
        [51, 52, 54, 55, 56],
    )
    overrides["health"] = _available_condition(
        "health",
        (
            "Za SK je določena III. stopnja varstva pred hrupom in I. stopnja "
            "varstva pred elektromagnetnim sevanjem. Pri stanovanjskih prostorih "
            "je treba praviloma zagotoviti predpisano osončenje vsaj enega "
            "prostora za bivanje. Upoštevati je treba še zahteve glede vodnih "
            "virov, svetlobnega onesnaževanja, neoviranega dostopa in požarne varnosti."
        ),
        ordinance,
        [57, 63, 64, 65],
    )
    return overrides


def _available_condition(
    key: str,
    description: str,
    source: PlanningTextSource,
    pages: list[int],
) -> PlanningCondition:
    topic = next(item for item in TOPICS if item.key == key)
    return PlanningCondition(
        key=topic.key,
        title=topic.title,
        description=description.strip(),
        available=True,
        source_title=source.title,
        source_url=source.url,
        pages=pages,
    )


def _extract_kranj_eup_profile(
    source: PlanningTextSource, planning_unit: str
) -> _KranjEUPProfile | None:
    target = _normalize(planning_unit)
    columns = (
        (20, 45),
        (45, 67),
        (67, 80),
        (80, 104),
        (104, 121),
        (121, 147),
        (147, 180),
        (180, 202),
        (202, None),
    )
    for page_number, raw_page in enumerate(source.pages, start=1):
        lines = raw_page.splitlines()
        for index, line in enumerate(lines):
            if _normalize(line[columns[0][0] : columns[0][1]]) != target:
                continue
            def value(column: tuple[int, int | None]) -> str:
                return (
                    re.sub(r"\s+", " ", line[column[0] : column[1]]).strip()
                    or "/"
                )

            def continued_value(column: tuple[int, int | None]) -> str:
                parts = [value(column)]
                cursor = index - 1
                while cursor >= 0:
                    previous = lines[cursor]
                    if _normalize(previous[20:45]) or _normalize(previous[45:67]):
                        break
                    part = re.sub(
                        r"\s+", " ", previous[column[0] : column[1]]
                    ).strip()
                    if not part:
                        break
                    parts.insert(0, part)
                    cursor -= 1
                cursor = index + 1
                while cursor < len(lines):
                    following = lines[cursor]
                    if _normalize(following[20:45]) or _normalize(following[45:67]):
                        break
                    part = re.sub(
                        r"\s+", " ", following[column[0] : column[1]]
                    ).strip()
                    if not part:
                        break
                    parts.append(part)
                    cursor += 1
                return " ".join(parts).strip() or "/"

            return _KranjEUPProfile(
                planning_unit=re.sub(r"\s+", " ", line[20:45]).strip(),
                land_use=value(columns[1]),
                max_fi=value(columns[2]),
                max_fz=continued_value(columns[3]),
                min_open_space=value(columns[4]),
                max_floors=value(columns[5]),
                urban_form=value(columns[6]),
                regulation_method=value(columns[7]),
                special_provision=value(columns[8]),
                source=source,
                page_number=page_number,
            )
    return None


def _find_land_use_section(
    source: PlanningTextSource, land_use_code: str
) -> tuple[str, int] | None:
    code = re.escape(_normalize(land_use_code))
    pattern = re.compile(rf"^\(\d+\)\s+{code}\s*[-–]")
    for page_number, raw_page in enumerate(source.pages, start=1):
        lines = _single_column_lines(raw_page)
        for index, line in enumerate(lines):
            if pattern.search(_normalize(line)):
                return _excerpt(lines, index), page_number
    return None


def _find_exact_section(
    source: PlanningTextSource,
    aliases: tuple[str, ...],
    *,
    page_hints: range | None = None,
) -> tuple[str, int] | None:
    normalized_aliases = tuple(_normalize(alias) for alias in aliases)
    for page_number, raw_page in enumerate(source.pages, start=1):
        if page_hints is not None and page_number not in page_hints:
            continue
        lines = _single_column_lines(raw_page)
        for index, line in enumerate(lines):
            normalized_line = _normalize(line)
            if any(alias in normalized_line for alias in normalized_aliases):
                excerpt = _excerpt(lines, index)
                if len(excerpt) < 70:
                    excerpt = _section_excerpt(lines, index)
                if len(excerpt) >= 70:
                    return excerpt, page_number
    return None


def _section_excerpt(lines: list[str], start: int) -> str:
    """Keep short nested headings until the first substantive section text."""
    selected: list[str] = []
    length = 0
    for line in lines[start : start + 18]:
        if selected and length >= 240 and _looks_like_new_major_heading(line):
            break
        selected.append(line)
        length += len(line) + 1
        if length >= 1_050:
            break
    result = " ".join(selected)
    if len(result) > 1_100:
        return result[:1_097].rstrip() + "…"
    return result


def _single_column_lines(raw_text: str) -> list[str]:
    dehyphenated = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", raw_text)
    return [
        cleaned
        for line in dehyphenated.splitlines()
        if (cleaned := re.sub(r"\s+", " ", line).strip())
    ]


def _display_optional(value: str) -> str:
    return "ni posebej določen" if value.strip() == "/" else value


def _display_fz(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip(" ,")
    if "stan. hiše" in normalized and "kmetije" in normalized:
        return normalized.replace("stan. hiše", "stanovanjske hiše")
    return _display_optional(normalized)


def extract_preemption_right(
    sources: list[PlanningTextSource],
    contexts: list[PlanningContext],
    municipality: str | None = None,
) -> PreemptionRightAssessment:
    """Find municipal pre-emption provisions without inferring a spatial match."""
    municipal_result = _municipal_preemption_right(municipality, contexts, len(sources))
    if municipal_result is not None:
        return municipal_result
    if not sources:
        return PreemptionRightAssessment(
            status="unavailable",
            label="Samodejni pregled ni bil mogoč",
            detail=(
                "Besedilni del veljavnega OPN oziroma občinskega odloka ni bil "
                "na voljo za pregled. Podatek je treba pridobiti pri pristojni občini."
            ),
            legal_basis=_PREEMPTION_LEGAL_BASIS,
        )

    context_terms = _context_terms(contexts)
    candidates: list[tuple[float, str, PlanningTextSource, int, tuple[str, ...]]] = []
    for source in sources:
        for page_number, raw_page in enumerate(source.pages, start=1):
            lines = _clean_lines(raw_page)
            if not lines:
                continue
            normalized_lines = [_normalize(line) for line in lines]
            normalized_page = " ".join(normalized_lines)
            if "predkupn" not in normalized_page or "pravic" not in normalized_page:
                continue
            matched_terms = tuple(
                term for term in context_terms if _contains_context_term(normalized_page, term)
            )
            for line_index, normalized_line in enumerate(normalized_lines):
                if "predkupn" not in normalized_line or "pravic" not in normalized_line:
                    continue
                excerpt = _preemption_excerpt(lines, line_index)
                normalized_excerpt = _normalize(excerpt)
                score = 4 + min(len(excerpt), 950) / 950
                if "obmocje predkupne pravice" in normalized_excerpt:
                    score += 9
                if "uveljavlja predkupno pravico" in normalized_excerpt:
                    score += 8
                if "odlok o predkupni pravici" in normalized_excerpt:
                    score += 8
                if "stavbn" in normalized_excerpt or "ureditven" in normalized_excerpt:
                    score += 4
                if "odlok" in _normalize(source.title):
                    score += 3
                score += min(len(matched_terms), 3) * 2
                candidates.append(
                    (score, excerpt, source, page_number, matched_terms[:4])
                )

    if not candidates:
        return PreemptionRightAssessment(
            status="not_found",
            label="Določba v pregledanih dokumentih ni bila zaznana",
            detail=(
                f"Pregledanih je bilo {len(sources)} besedilnih dokumentov veljavnih "
                "prostorskih aktov. Odsotnost besedilnega zadetka ne izključuje "
                "posebnega občinskega odloka ali druge veljavne evidence."
            ),
            legal_basis=_PREEMPTION_LEGAL_BASIS,
            checked_document_count=len(sources),
        )

    _, excerpt, source, page_number, matched_terms = max(
        candidates, key=lambda item: item[0]
    )
    if matched_terms:
        context_note = (
            "Na isti strani so zaznane tudi oznake prostorskega konteksta parcele "
            f"({', '.join(matched_terms)}). "
        )
    else:
        context_note = "Neposredna povezava določbe z oznako EUP ali namensko rabo parcele ni bila zaznana. "
    return PreemptionRightAssessment(
        status="provision_found",
        label="V prostorskem aktu je zaznana določba o predkupni pravici",
        detail=(
            context_note
            + "Besedilni zadetek sam ne dokazuje, da občinsko območje predkupne "
            "pravice geometrijsko vključuje parcelo; pred prodajo je potrebno potrdilo občine."
        ),
        legal_basis=_PREEMPTION_LEGAL_BASIS,
        source_title=source.title,
        source_url=source.url,
        pages=[page_number],
        excerpt=excerpt,
        checked_document_count=len(sources),
    )


def _municipal_preemption_right(
    municipality: str | None,
    contexts: list[PlanningContext],
    checked_document_count: int,
) -> PreemptionRightAssessment | None:
    normalized_municipality = _normalize(municipality or "").replace("–", "-")
    if normalized_municipality not in {"gorenja vas-poljane", "gorenja vas - poljane"}:
        return None

    matching_uses = [
        context
        for context in contexts
        if (context.land_use_code or "").strip().upper() in _BUILDING_LAND_CODES
    ]
    legal_basis = (
        "Odlok o predkupni pravici Občine Gorenja vas - Poljane "
        "(Uradni list RS, št. 67/2021), 2. in 3. člen; "
        "199.–201. člen ZUreP-3"
    )
    source_title = (
        "Odlok o predkupni pravici Občine Gorenja vas - Poljane "
        "(Uradni list RS, št. 67/2021)"
    )
    excerpt = (
        "2. in 3. člen odloka določata območje predkupne pravice tudi na "
        "stavbnih zemljiščih, kot so določena v veljavnih občinskih prostorskih aktih."
    )
    if matching_uses:
        use_descriptions = []
        for context in matching_uses:
            code = (context.land_use_code or "").strip().upper()
            share = (
                f" ({context.parcel_share_percent:g} % parcele)"
                if context.parcel_share_percent is not None
                else ""
            )
            use_descriptions.append(
                f"{code} – {context.land_use_description or 'stavbno zemljišče'}{share}"
            )
        return PreemptionRightAssessment(
            status="applies",
            label="Parcela posega v območje predkupne pravice občine",
            detail=(
                "Veljavni občinski odlok vključuje stavbna zemljišča, prostorski "
                f"presek PIS pa je na parceli zaznal: {'; '.join(use_descriptions)}. "
                "To je avtomatizirana ugotovitev na podlagi javnih evidenc; pred "
                "prodajo pridobite uradno izjavo občine o uveljavljanju pravice."
            ),
            legal_basis=legal_basis,
            source_title=source_title,
            source_url=_GORENJA_VAS_PREEMPTION_URL,
            excerpt=excerpt,
            checked_document_count=checked_document_count,
        )

    return PreemptionRightAssessment(
        status="provision_found",
        label="Za občino je bil najden veljavni odlok o predkupni pravici",
        detail=(
            "Odlok poleg stavbnih zemljišč vključuje tudi nekatera druga območja, "
            "vendar razpoložljivi prostorski presek ne zadošča za zanesljivo "
            "parcelno ugotovitev. Potrebna je uradna izjava občine."
        ),
        legal_basis=legal_basis,
        source_title=source_title,
        source_url=_GORENJA_VAS_PREEMPTION_URL,
        excerpt=excerpt,
        checked_document_count=checked_document_count,
    )


def _contains_context_term(normalized_text: str, term: str) -> bool:
    if len(term) <= 3:
        return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized_text))
    return term in normalized_text


def _preemption_excerpt(lines: list[str], match_index: int) -> str:
    start = max(0, match_index - 1)
    selected = lines[start : match_index + 10]
    excerpt = " ".join(selected)
    if len(excerpt) > 1_100:
        return excerpt[:1_097].rstrip() + "…"
    return excerpt


def _best_candidate(
    topic: _Topic,
    sources: list[PlanningTextSource],
    context_terms: tuple[str, ...],
) -> tuple[str, PlanningTextSource, int] | None:
    candidates: list[tuple[float, str, PlanningTextSource, int]] = []
    for source in sources:
        for page_number, raw_page in enumerate(source.pages, start=1):
            lines = _clean_lines(raw_page)
            if not lines:
                continue
            normalized_lines = [_normalize(line) for line in lines]
            for alias_index, alias in enumerate(topic.aliases):
                normalized_alias = _normalize(alias)
                for line_index, normalized_line in enumerate(normalized_lines):
                    if normalized_alias not in normalized_line:
                        continue
                    excerpt = _excerpt(lines, line_index)
                    if len(excerpt) < 70:
                        continue
                    normalized_excerpt = _normalize(excerpt)
                    context_score = sum(
                        _contains_context_term(normalized_excerpt, term)
                        for term in context_terms
                    )
                    exact_heading = normalized_line == normalized_alias
                    normalized_title = _normalize(source.title)
                    score = (
                        len(normalized_alias) / 20
                        + (len(topic.aliases) - alias_index) * 4
                        + context_score * 3
                        + min(len(excerpt), 900) / 900
                        + (3 if exact_heading else 0)
                        + (1.5 if "odlok" in normalized_title else 0)
                        + (4 if "precisceno besedilo" in normalized_title else 0)
                        - (2 if "spremembe" in normalized_title else 0)
                    )
                    candidates.append((score, excerpt, source, page_number))
                    break
    if not candidates:
        return None
    _, excerpt, source, page_number = max(candidates, key=lambda item: item[0])
    return excerpt, source, page_number


def _clean_lines(raw_text: str) -> list[str]:
    raw_lines = raw_text.splitlines()
    divider_candidates: list[float] = []
    for line in raw_lines:
        for gap in re.finditer(r" {8,}", line):
            if line[: gap.start()].strip() and line[gap.end() :].strip():
                midpoint = (gap.start() + gap.end()) / 2
                if 45 <= midpoint <= 115:
                    divider_candidates.append(midpoint)
    if len(divider_candidates) >= 8:
        divider = round(statistics.median(divider_candidates))
        left_column = [line[:divider].rstrip() for line in raw_lines]
        right_column = [line[divider:].rstrip() for line in raw_lines]
        raw_text = "\n".join((*left_column, *right_column))
    dehyphenated = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", raw_text)
    return [
        cleaned
        for line in dehyphenated.splitlines()
        if (cleaned := re.sub(r"\s+", " ", line).strip())
    ]


def _excerpt(lines: list[str], start: int) -> str:
    selected: list[str] = []
    length = 0
    for line in lines[start : start + 12]:
        if selected and _looks_like_new_major_heading(line):
            break
        selected.append(line)
        length += len(line) + 1
        if length >= 1050:
            break
    result = " ".join(selected)
    if len(result) > 1100:
        result = result[:1097].rstrip() + "…"
    return result


def _looks_like_new_major_heading(line: str) -> bool:
    letters = [character for character in line if character.isalpha()]
    uppercase = bool(letters) and sum(character.isupper() for character in letters) / len(
        letters
    ) > 0.86
    numbered = bool(re.match(r"^\d+(?:\.\d+){0,3}[.)]?\s+\S", line))
    return len(line) <= 150 and (uppercase or numbered)


def _context_terms(contexts: list[PlanningContext]) -> tuple[str, ...]:
    values: set[str] = set()
    for context in contexts:
        for value in (
            context.planning_unit,
            context.subunit,
            context.land_use_description,
        ):
            if value and len(normalized := _normalize(value)) >= 3:
                values.add(normalized)
                words = normalized.split()
                if len(words) >= 2:
                    values.add(" ".join(words[-2:]))
        if context.land_use_code and len(context.land_use_code.strip()) >= 2:
            values.add(_normalize(context.land_use_code))
    return tuple(sorted(values, key=len, reverse=True))


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip()
