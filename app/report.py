from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pymupdf

from .models import (
    LandUseAssessmentItem,
    PlanningMapEvidence,
    SearchResult,
    SpatialFinding,
)


OFFICIAL_TEMPLATE_URL = "https://pisrs.si/api/datoteke/integracije/351620891"

NAVY = (13 / 255, 32 / 255, 56 / 255)
NAVY_SOFT = (23 / 255, 57 / 255, 87 / 255)
ORANGE = (244 / 255, 122 / 255, 36 / 255)
TEAL = (15 / 255, 152 / 255, 150 / 255)
BLUE = (60 / 255, 131 / 255, 219 / 255)
INK = (20 / 255, 36 / 255, 58 / 255)
MUTED = (91 / 255, 112 / 255, 132 / 255)
LINE = (219 / 255, 227 / 255, 235 / 255)
PAPER = (1, 1, 1)
CANVAS = (247 / 255, 249 / 255, 251 / 255)
PALE_ORANGE = (1, 242 / 255, 232 / 255)
PALE_TEAL = (229 / 255, 247 / 255, 245 / 255)
PALE_BLUE = (234 / 255, 242 / 255, 252 / 255)

A4_WIDTH = 595.28
A4_HEIGHT = 841.89
LEFT = 42
RIGHT = 42
CONTENT_WIDTH = A4_WIDTH - LEFT - RIGHT


@dataclass(frozen=True)
class ReportField:
    label: str
    value: str


@dataclass(frozen=True)
class ReportSection:
    number: int
    title: str
    status: str
    fields: tuple[ReportField, ...]
    hint: str


class _Fonts:
    def __init__(self) -> None:
        regular_path = _font_path("DejaVuSans.ttf")
        bold_path = _font_path("DejaVuSans-Bold.ttf")
        if regular_path and bold_path:
            self.regular_name = "PropioRegular"
            self.bold_name = "PropioBold"
            self.regular_path = str(regular_path)
            self.bold_path = str(bold_path)
            self.regular = pymupdf.Font(fontfile=self.regular_path)
            self.bold = pymupdf.Font(fontfile=self.bold_path)
        else:
            self.regular_name = "helv"
            self.bold_name = "hebo"
            self.regular_path = None
            self.bold_path = None
            self.regular = pymupdf.Font("helv")
            self.bold = pymupdf.Font("hebo")

    def install(self, page: pymupdf.Page) -> None:
        if self.regular_path:
            page.insert_font(fontname=self.regular_name, fontfile=self.regular_path)
            page.insert_font(fontname=self.bold_name, fontfile=self.bold_path)


class _ReportWriter:
    def __init__(self, title: str) -> None:
        self.document = pymupdf.open()
        self.fonts = _Fonts()
        self.title = title
        self.page: pymupdf.Page
        self.y = 0.0
        self._new_page(first=True)

    def _new_page(self, *, first: bool = False) -> None:
        self.page = self.document.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        self.fonts.install(self.page)
        self.page.draw_rect(self.page.rect, color=CANVAS, fill=CANVAS, width=0)
        if first:
            self._cover_header()
            self.y = 164
        else:
            self.page.draw_rect(pymupdf.Rect(0, 0, A4_WIDTH, 43), color=NAVY, fill=NAVY, width=0)
            self._text(LEFT, 18, "Propio", 10, bold=True, color=PAPER)
            self._text(78, 18, "scan", 10, bold=True, color=ORANGE)
            self._text(LEFT, 33, self.title, 6.8, color=(0.72, 0.79, 0.86))
            self.y = 62

    def _cover_header(self) -> None:
        self.page.draw_rect(pymupdf.Rect(0, 0, A4_WIDTH, 138), color=NAVY, fill=NAVY, width=0)
        self.page.draw_rect(pymupdf.Rect(LEFT, 31, LEFT + 28, 59), color=ORANGE, fill=ORANGE, width=0)
        self.page.draw_rect(pymupdf.Rect(LEFT + 7, 38, LEFT + 21, 52), color=PAPER, width=1.2)
        self._text(80, 38, "Propio", 17, bold=True, color=PAPER)
        self._text(130, 38, "scan", 17, bold=True, color=ORANGE)
        self._text(80, 54, "PODATKI. JASNE ODLOČITVE.", 6.5, bold=True, color=(0.69, 0.77, 0.84))
        self._text(LEFT, 84, "PREDIZPOLNJEN PREGLED", 8.2, bold=True, color=ORANGE)
        self._text(LEFT, 106, "Lokacijska informacija", 22, bold=True, color=PAPER)
        self._text(LEFT, 124, "Informativni izpis po strukturi uradne Priloge 2", 8.5, color=(0.76, 0.82, 0.88))
        badge = pymupdf.Rect(A4_WIDTH - RIGHT - 113, 94, A4_WIDTH - RIGHT, 118)
        self.page.draw_rect(badge, color=ORANGE, fill=ORANGE, width=0)
        self._text(badge.x0 + 10, badge.y0 + 16, "NI URADNA LISTINA", 7.2, bold=True, color=PAPER)

    def _text(
        self,
        x: float,
        y: float,
        text: str,
        size: float,
        *,
        bold: bool = False,
        color: tuple[float, float, float] = INK,
    ) -> None:
        self.page.insert_text(
            pymupdf.Point(x, y),
            text,
            fontsize=size,
            fontname=self.fonts.bold_name if bold else self.fonts.regular_name,
            color=color,
        )

    def _wrapped(
        self,
        text: str,
        width: float,
        size: float,
        *,
        bold: bool = False,
    ) -> list[str]:
        font = self.fonts.bold if bold else self.fonts.regular
        lines: list[str] = []
        for paragraph in (text or "—").splitlines() or ["—"]:
            words: list[str] = []
            for token in paragraph.split() or ["—"]:
                if font.text_length(token, fontsize=size) <= width:
                    words.append(token)
                    continue
                chunk = ""
                for character in token:
                    candidate = chunk + character
                    if chunk and font.text_length(candidate, fontsize=size) > width:
                        words.append(chunk)
                        chunk = character
                    else:
                        chunk = candidate
                if chunk:
                    words.append(chunk)
            current = words[0]
            for word in words[1:]:
                candidate = f"{current} {word}"
                if font.text_length(candidate, fontsize=size) <= width:
                    current = candidate
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
        return lines

    def _ensure(self, height: float) -> None:
        if self.y + height > A4_HEIGHT - 42:
            self._new_page()

    def meta(self, result: SearchResult) -> None:
        parcel = result.parcel
        municipality = parcel.municipality or "Občina ni določena"
        cadastral = parcel.cadastral_municipality or "ime ni na voljo"
        reference = f"{parcel.cadastral_municipality_id} {parcel.parcel_number}"
        completed = result.completed_at.strftime("%d. %m. %Y ob %H:%M UTC")
        self._ensure(81)
        rect = pymupdf.Rect(LEFT, self.y, A4_WIDTH - RIGHT, self.y + 69)
        self.page.draw_rect(rect, color=LINE, fill=PAPER, width=0.8)
        self.page.draw_rect(pymupdf.Rect(rect.x0, rect.y0, rect.x0 + 5, rect.y1), color=TEAL, fill=TEAL, width=0)
        self._text(rect.x0 + 18, rect.y0 + 20, "PARCELA", 6.5, bold=True, color=MUTED)
        self._text(rect.x0 + 18, rect.y0 + 39, reference, 15, bold=True)
        self._text(rect.x0 + 18, rect.y0 + 55, f"k. o. {parcel.cadastral_municipality_id} – {cadastral}", 7.5, color=MUTED)
        divider = rect.x0 + 230
        self.page.draw_line((divider, rect.y0 + 13), (divider, rect.y1 - 13), color=LINE, width=0.8)
        self._text(divider + 18, rect.y0 + 20, "OBMOČJE IN ČAS IZPISA", 6.5, bold=True, color=MUTED)
        self._text(divider + 18, rect.y0 + 39, municipality, 10, bold=True)
        self._text(divider + 18, rect.y0 + 55, f"Analiza zaključena {completed}", 7.2, color=MUTED)
        self.y = rect.y1 + 14

        warning = (
            "Ta dokument je avtomatsko pripravljen informativni pregled javnih evidenc. "
            "Ni lokacijska informacija, upravna odločba ali potrdilo občine. Polja z oznako "
            "»preverite pri občini« niso bila ugotovljena iz razpoložljivih virov."
        )
        lines = self._wrapped(warning, CONTENT_WIDTH - 28, 7.8)
        height = 25 + len(lines) * 10
        rect = pymupdf.Rect(LEFT, self.y, A4_WIDTH - RIGHT, self.y + height)
        self.page.draw_rect(rect, color=(0.96, 0.77, 0.57), fill=PALE_ORANGE, width=0.8)
        self._text(rect.x0 + 14, rect.y0 + 17, "POMEMBNO", 7, bold=True, color=(0.74, 0.31, 0.04))
        for index, line in enumerate(lines):
            self._text(rect.x0 + 14, rect.y0 + 32 + index * 10, line, 7.8, color=(0.42, 0.25, 0.13))
        self.y = rect.y1 + 18

    def section(
        self,
        section: ReportSection,
        *,
        map_image_path: Path | None = None,
        map_evidence: PlanningMapEvidence | None = None,
        legend_items: list[LandUseAssessmentItem] | None = None,
    ) -> None:
        estimated_height = 47.0
        for field in section.fields:
            label_lines = self._wrapped(field.label.upper(), 126, 6.4, bold=True)
            value_lines = self._wrapped(field.value, CONTENT_WIDTH - 174, 8.2)
            estimated_height += max(34, 18 + max(len(label_lines), len(value_lines)) * 10)
        hint_lines = self._wrapped(section.hint, CONTENT_WIDTH - 36, 7.1)
        estimated_height += 41 + len(hint_lines) * 9
        self._ensure(estimated_height if estimated_height < 690 else 145)
        header_y = self.y
        self.page.draw_rect(
            pymupdf.Rect(LEFT, header_y, A4_WIDTH - RIGHT, header_y + 39),
            color=NAVY,
            fill=NAVY,
            width=0,
        )
        self.page.draw_rect(
            pymupdf.Rect(LEFT, header_y, LEFT + 39, header_y + 39),
            color=ORANGE,
            fill=ORANGE,
            width=0,
        )
        label, badge_fill, badge_text = {
            "automatic": ("SAMODEJNO IZPOLNJENO", TEAL, PAPER),
            "review": ("PREVERITE PRI OBČINI", ORANGE, PAPER),
            "partial": ("DELNO IZPOLNJENO", BLUE, PAPER),
        }[section.status]
        badge_width = self.fonts.bold.text_length(label, fontsize=6.2) + 18
        badge = pymupdf.Rect(
            A4_WIDTH - RIGHT - badge_width,
            header_y + 10,
            A4_WIDTH - RIGHT - 9,
            header_y + 29,
        )
        title_size = 10.3
        title_width = badge.x0 - (LEFT + 52) - 11
        while self.fonts.bold.text_length(section.title, fontsize=title_size) > title_width and title_size > 7.2:
            title_size -= 0.2
        self._text(LEFT + 14, header_y + 26, str(section.number), 13, bold=True, color=PAPER)
        self._text(LEFT + 52, header_y + 25, section.title, title_size, bold=True, color=PAPER)
        self.page.draw_rect(badge, color=badge_fill, fill=badge_fill, width=0)
        self._text(badge.x0 + 9, badge.y0 + 13, label, 6.2, bold=True, color=badge_text)
        self.y = header_y + 47

        for index, field in enumerate(section.fields):
            label_lines = self._wrapped(field.label.upper(), 126, 6.4, bold=True)
            value_lines = self._wrapped(field.value, CONTENT_WIDTH - 174, 8.2)
            line_count = max(len(label_lines), len(value_lines))
            height = max(34, 18 + line_count * 10)
            self._ensure(height + 2)
            fill = PAPER if index % 2 == 0 else (0.97, 0.98, 0.99)
            rect = pymupdf.Rect(LEFT, self.y, A4_WIDTH - RIGHT, self.y + height)
            self.page.draw_rect(rect, color=LINE, fill=fill, width=0.55)
            self.page.draw_line((LEFT + 151, self.y), (LEFT + 151, self.y + height), color=LINE, width=0.55)
            for line_index, line in enumerate(label_lines):
                self._text(LEFT + 12, self.y + 20 + line_index * 9, line, 6.4, bold=True, color=MUTED)
            for line_index, line in enumerate(value_lines):
                self._text(LEFT + 164, self.y + 20 + line_index * 10, line, 8.2)
            self.y += height

        if map_image_path and map_evidence:
            self._planning_map_attachment(
                map_image_path,
                map_evidence,
                legend_items or [],
            )

        hint_height = 21 + len(hint_lines) * 9
        self._ensure(hint_height + 20)
        hint_fill = PALE_ORANGE if section.status == "review" else PALE_BLUE
        hint_color = (0.58, 0.30, 0.10) if section.status == "review" else (0.22, 0.37, 0.54)
        rect = pymupdf.Rect(LEFT, self.y, A4_WIDTH - RIGHT, self.y + hint_height)
        self.page.draw_rect(rect, color=LINE, fill=hint_fill, width=0.55)
        self._text(LEFT + 12, self.y + 16, "NAMIG", 6.3, bold=True, color=hint_color)
        for line_index, line in enumerate(hint_lines):
            self._text(LEFT + 48, self.y + 16 + line_index * 9, line, 7.1, color=hint_color)
        self.y = rect.y1 + 20

    def _planning_map_attachment(
        self,
        image_path: Path,
        evidence: PlanningMapEvidence,
        legend_items: list[LandUseAssessmentItem],
    ) -> None:
        pixmap = pymupdf.Pixmap(str(image_path))
        aspect_ratio = pixmap.height / max(pixmap.width, 1)
        pixmap = None
        available_width = CONTENT_WIDTH - 24
        image_height = min(330.0, available_width * aspect_ratio)
        image_width = image_height / max(aspect_ratio, 0.01)
        attachment_height = 31 + image_height + 40
        self._ensure(attachment_height + 12)

        top = self.y + 8
        self.page.draw_rect(
            pymupdf.Rect(LEFT, top, A4_WIDTH - RIGHT, top + 31),
            color=NAVY_SOFT,
            fill=NAVY_SOFT,
            width=0,
        )
        self._text(LEFT + 12, top + 20, "IZRIS IZ PROSTORSKEGA REDA", 8, bold=True, color=PAPER)
        self._text(A4_WIDTH - RIGHT - 78, top + 20, f"STRAN {evidence.page}", 6.2, bold=True, color=ORANGE)

        image_top = top + 31
        image_left = LEFT + (CONTENT_WIDTH - image_width) / 2
        image_rect = pymupdf.Rect(
            image_left,
            image_top,
            image_left + image_width,
            image_top + image_height,
        )
        self.page.draw_rect(
            pymupdf.Rect(LEFT, image_top, A4_WIDTH - RIGHT, image_top + image_height),
            color=LINE,
            fill=PAPER,
            width=0.6,
        )
        self.page.insert_image(image_rect, filename=str(image_path), keep_proportion=True)

        caption_top = image_top + image_height
        self.page.draw_rect(
            pymupdf.Rect(LEFT, caption_top, A4_WIDTH - RIGHT, caption_top + 40),
            color=LINE,
            fill=(0.97, 0.98, 0.99),
            width=0.6,
        )
        title = _clip(evidence.pdf_title, 78)
        self._text(LEFT + 12, caption_top + 16, title, 7.2, bold=True)
        self._text(
            LEFT + 12,
            caption_top + 30,
            _clip(f"{evidence.act_title} · rdeča linija označuje obris iskane parcele", 105),
            6.5,
            color=MUTED,
        )
        self.y = caption_top + 48
        self._planning_map_legend(legend_items)

    def _planning_map_legend(self, items: list[LandUseAssessmentItem]) -> None:
        self._ensure(44)
        self._text(LEFT, self.y + 12, "LEGENDA PRIKAZA", 7.1, bold=True, color=MUTED)
        self.y += 20
        rows: list[tuple[str, str, tuple[float, float, float]]] = [
            ("PARCELA", "Rdeča linija – obris iskane parcele", (0.80, 0.17, 0.14)),
        ]
        for item in items:
            measures = []
            if item.parcel_share_percent is not None:
                measures.append(f"{item.parcel_share_percent:g} % parcele")
            if item.parcel_area_m2 is not None:
                measures.append(f"{item.parcel_area_m2:g} m²")
            detail = item.name
            if measures:
                detail += f" · {' · '.join(measures)}"
            rows.append((item.code or "NRP", detail, TEAL))

        for label, value, swatch in rows:
            value_lines = self._wrapped(value, CONTENT_WIDTH - 112, 7)
            row_height = max(25, 12 + len(value_lines) * 9)
            self._ensure(row_height)
            self.page.draw_rect(
                pymupdf.Rect(LEFT, self.y, A4_WIDTH - RIGHT, self.y + row_height),
                color=LINE,
                fill=PAPER,
                width=0.5,
            )
            self.page.draw_rect(
                pymupdf.Rect(LEFT + 10, self.y + 8, LEFT + 22, self.y + 20),
                color=swatch,
                fill=swatch,
                width=0,
            )
            self._text(LEFT + 31, self.y + 17, label, 6.5, bold=True, color=MUTED)
            for line_index, line in enumerate(value_lines):
                self._text(LEFT + 100, self.y + 17 + line_index * 9, line, 7)
            self.y += row_height
        self.y += 8

    def closing(self, result: SearchResult) -> None:
        self._ensure(185)
        self.page.draw_rect(
            pymupdf.Rect(LEFT, self.y, A4_WIDTH - RIGHT, self.y + 33),
            color=NAVY_SOFT,
            fill=NAVY_SOFT,
            width=0,
        )
        self._text(LEFT + 13, self.y + 21, "VIRI, TAKSA IN ODGOVORNOST", 8.5, bold=True, color=PAPER)
        self.y += 42
        entries = [
            ("Uporabljeni viri", "GURS / E-prostor, PIS, GeoHub Slovenija, eVRD in povezani javno objavljeni prostorski dokumenti."),
            ("Upravna taksa", "Ni plačana. Ta izpis je rezultat digitalne analize in ni občinska upravna storitev."),
            ("Uradna predloga", OFFICIAL_TEMPLATE_URL),
            ("Opozorila analize", " | ".join(result.warnings) if result.warnings else "Analiza ni vrnila dodatnih sistemskih opozoril."),
        ]
        for label, value in entries:
            value = _clip(value, 900)
            lines = self._wrapped(value, CONTENT_WIDTH - 145, 7.4)
            height = max(28, 13 + len(lines) * 9)
            self._ensure(height)
            self._text(LEFT, self.y + 16, label.upper(), 6.2, bold=True, color=MUTED)
            for line_index, line in enumerate(lines):
                self._text(LEFT + 136, self.y + 16 + line_index * 9, line, 7.4)
            self.page.draw_line((LEFT, self.y + height), (A4_WIDTH - RIGHT, self.y + height), color=LINE, width=0.5)
            self.y += height

        self.y += 16
        statement = (
            "Za uradno lokacijsko informacijo vložite zahtevo pri pristojni občini. "
            "Pred projektiranjem ali pravnim poslom preverite veljavne prostorske akte, "
            "zemljiško knjigo, upravne evidence in pogoje pristojnih nosilcev urejanja prostora."
        )
        lines = self._wrapped(statement, CONTENT_WIDTH - 30, 8)
        height = 28 + len(lines) * 11
        self._ensure(height)
        rect = pymupdf.Rect(LEFT, self.y, A4_WIDTH - RIGHT, self.y + height)
        self.page.draw_rect(rect, color=TEAL, fill=PALE_TEAL, width=0.8)
        for index, line in enumerate(lines):
            self._text(LEFT + 15, self.y + 23 + index * 11, line, 8, bold=index == 0, color=(0.07, 0.35, 0.36))
        self.y = rect.y1 + 15

    def finish(self) -> bytes:
        page_total = self.document.page_count
        for index, page in enumerate(self.document):
            self.fonts.install(page)
            page.draw_line((LEFT, A4_HEIGHT - 29), (A4_WIDTH - RIGHT, A4_HEIGHT - 29), color=LINE, width=0.6)
            page.insert_text(
                pymupdf.Point(LEFT, A4_HEIGHT - 16),
                "Propioscan · informativni pregled javnih evidenc · propioscan.com",
                fontsize=6.2,
                fontname=self.fonts.regular_name,
                color=MUTED,
            )
            page.insert_text(
                pymupdf.Point(A4_WIDTH - RIGHT - 38, A4_HEIGHT - 16),
                f"{index + 1} / {page_total}",
                fontsize=6.2,
                fontname=self.fonts.bold_name,
                color=MUTED,
            )
        self.document.set_metadata(
            {
                "title": self.title,
                "author": "Propioscan",
                "subject": "Informativni pregled po strukturi lokacijske informacije",
                "keywords": "Propioscan, parcela, lokacijska informacija, GURS, PIS",
            }
        )
        output = self.document.tobytes(garbage=4, deflate=True)
        self.document.close()
        return output


def generate_location_report(
    result: SearchResult,
    map_preview_path: Path | None = None,
) -> bytes:
    parcel = result.parcel
    reference = f"{parcel.cadastral_municipality_id} {parcel.parcel_number}"
    title = f"Propioscan – informativni pregled parcele {reference}"
    writer = _ReportWriter(title)
    writer.meta(result)
    map_evidence = _report_map_evidence(result) if map_preview_path else None
    legend_items = result.land_use_assessment.items if result.land_use_assessment else []
    for section in build_report_sections(result):
        writer.section(
            section,
            map_image_path=map_preview_path if section.number == 9 else None,
            map_evidence=map_evidence if section.number == 9 else None,
            legend_items=legend_items if section.number == 9 else None,
        )
    writer.closing(result)
    return writer.finish()


def report_filename(result: SearchResult) -> str:
    raw = f"propioscan-lokacijska-informacija-{result.parcel.cadastral_municipality_id}-{result.parcel.parcel_number}"
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-.")
    return f"{safe}.pdf"


def resolve_report_map_preview(result: SearchResult, data_dir: Path) -> Path | None:
    evidence = _report_map_evidence(result)
    if evidence is None or not evidence.preview_url:
        return None
    route = urlsplit(evidence.preview_url).path
    match = re.fullmatch(r"/api/map-previews/(\d+)/([^/]+)", route)
    if match is None:
        return None
    act_id, filename = match.groups()
    root = (Path(data_dir) / "map_previews" / act_id).resolve()
    candidate = (root / Path(unquote(filename)).name).resolve()
    if (
        candidate.parent != root
        or candidate.suffix.lower() != ".png"
        or not candidate.is_file()
    ):
        return None
    return candidate


def _report_map_evidence(result: SearchResult) -> PlanningMapEvidence | None:
    planning_map = result.planning_land_use_map
    if planning_map is None or not planning_map.evidence:
        return None
    return next(
        (evidence for evidence in planning_map.evidence if evidence.preview_url),
        planning_map.evidence[0],
    )


def build_report_sections(result: SearchResult) -> tuple[ReportSection, ...]:
    parcel = result.parcel
    reference = f"{parcel.cadastral_municipality_id} {parcel.parcel_number}"
    cadastral_name = parcel.cadastral_municipality or "Ime katastrske občine ni bilo vrnjeno"

    land_fields: list[ReportField] = []
    contexts = result.planning_context
    if contexts:
        for index, context in enumerate(contexts, start=1):
            location = [
                label
                for label in (
                    context.planning_unit and f"EUP {context.planning_unit}",
                    context.subunit and f"PEUP {context.subunit}",
                )
                if label
            ]
            share = (
                f" · {context.parcel_share_percent:g} % parcele"
                if context.parcel_share_percent is not None
                else ""
            )
            value = (
                f"{context.land_use_code or 'brez oznake'} – "
                f"{context.land_use_description or 'opis ni na voljo'}{share}"
            )
            if location:
                value += f" · {' / '.join(location)}"
            land_fields.append(ReportField(f"Del namenske rabe {index}", value))
    else:
        land_fields.append(ReportField("Namenska raba", "Strukturiran podatek ni bil vrnjen"))

    if result.land_use_assessment:
        land_fields.append(
            ReportField(
                "Orientacijska razlaga",
                f"{result.land_use_assessment.label}. {result.land_use_assessment.summary}",
            )
        )

    act_fields: list[ReportField] = []
    if result.planning_acts:
        for act in result.planning_acts:
            stage = "veljavni / zaključeni postopek" if act.preparation_state == "completed" else "akt ali postopek v pripravi"
            details = " · ".join(
                part for part in (act.act_type, act.status, stage) if part
            )
            act_fields.append(ReportField(act.title, details or "Status ni naveden"))
    else:
        act_fields.append(ReportField("Prostorski akti", "PIS ni vrnil akta s prostorskim presekom parcele"))

    regime_findings = [
        ("Varstvo narave", result.protected_areas),
        ("Kulturna dediščina", result.cultural_heritage),
        ("Pravne in prostorske omejitve", result.constraints),
        ("Naravne nevarnosti", result.risks),
    ]
    regime_fields: list[ReportField] = []
    for group, findings in regime_findings:
        if findings:
            regime_fields.extend(_finding_fields(group, findings))
        else:
            regime_fields.append(ReportField(group, "Preverjeni spletni sloji niso vrnili preseka"))

    assessment_codes = [
        item.code for item in (result.land_use_assessment.items if result.land_use_assessment else []) if item.code
    ]
    development_use = ", ".join(assessment_codes) if assessment_codes else "namenska raba ni bila strukturirano določena"

    document_fields: list[ReportField] = []
    for document in result.documents[:8]:
        findings = "; ".join(item.detail for item in document.important_findings[:3])
        content = findings or document.summary or "Dokument je na voljo v tehničnem pregledu"
        document_fields.append(ReportField(document.pdf_title, _clip(content, 700)))
    if not document_fields:
        document_fields.append(ReportField("Prostorski izvedbeni pogoji", "Samodejni izvleček ni bil pripravljen"))

    municipality = parcel.municipality or "občina ni bila določena"
    map_evidence = _report_map_evidence(result)
    has_map_preview = bool(map_evidence and map_evidence.preview_url)
    return (
        ReportSection(
            1,
            "ZEMLJIŠKA PARCELA, ZA KATERO SE IZDA PREGLED",
            "automatic",
            (
                ReportField("Šifra in ime katastrske občine", f"{parcel.cadastral_municipality_id} – {cadastral_name}"),
                ReportField("Številka zemljiške parcele", parcel.parcel_number),
                ReportField("Občina", municipality),
                ReportField("Površina", f"{parcel.area_m2:,} m²".replace(",", ".") if parcel.area_m2 is not None else "Podatek ni na voljo"),
            ),
            "Identifikacijo in površino primerjajte z aktualnim stanjem v katastru nepremičnin GURS.",
        ),
        ReportSection(
            2,
            "NAMENSKA RABA PROSTORA",
            "automatic" if contexts else "review",
            tuple(land_fields),
            "Deleži so geometrijski presek javnega sloja. Namenska raba sama ne ustvarja pravice graditi; preverite še prostorske izvedbene pogoje.",
        ),
        ReportSection(
            3,
            "VELJAVNI PROSTORSKI AKTI IN AKTI V PRIPRAVI",
            "automatic" if result.planning_acts else "review",
            tuple(act_fields),
            "Odprite povezane zapise PIS v spletnem poročilu in preverite datum veljavnosti, spremembe akta ter uradno besedilo odloka.",
        ),
        ReportSection(
            4,
            "ZAČASNI UKREPI",
            "review",
            (ReportField("Stanje", "Razpoložljivi avtomatski viri ne omogočajo zanesljive potrditve začasnih ukrepov"),),
            "Občino vprašajte, ali je za parcelo vzpostavljen začasni ukrep, kakšna je pravna podlaga ter čas njegovega trajanja.",
        ),
        ReportSection(
            5,
            "PREDKUPNA PRAVICA",
            "review",
            (ReportField("Stanje", "Predkupna pravica občine ali države ni bila samodejno potrjena oziroma izključena"),),
            "Pred pravnim poslom zahtevajte uradno izjavo pristojne občine in po potrebi preverite predkupno pravico države.",
        ),
        ReportSection(
            6,
            "PRAVNI REŽIMI",
            "partial",
            tuple(regime_fields),
            "Navedeni so preseki preverjenih javnih slojev. Odsotnost zadetka ne dokazuje odsotnosti vseh pravnih režimov; pravno podlago potrdi pristojni organ.",
        ),
        ReportSection(
            7,
            "RAZVOJNA STOPNJA NEPOZIDANEGA STAVBNEGA ZEMLJIŠČA IN TAKSA",
            "review",
            (
                ReportField("Zaznane oznake namenske rabe", development_use),
                ReportField("Razvojna stopnja", "Ni določljiva iz preverjenih avtomatskih virov"),
                ReportField("Taksa za neizkoriščeno stavbno zemljišče", "Območje plačevanja ni bilo potrjeno"),
            ),
            "Pri občini preverite, ali je parcela stavbno zemljišče, njeno uradno razvojno stopnjo in morebitno območje plačevanja takse.",
        ),
        ReportSection(
            8,
            "SOGLASJE ZA SPREMINJANJE MEJE PARCELE",
            "review",
            (ReportField("Stanje", "Obveznost pridobitve soglasja ni bila samodejno potrjena oziroma izključena"),),
            "Pred parcelacijo ali izravnavo meje preverite pri občini, ali je za območje potrebno soglasje in na kateri pravni podlagi.",
        ),
        ReportSection(
            9,
            "PRILOGA: IZSEK GRAFIČNEGA DELA PROSTORSKEGA AKTA",
            "automatic" if has_map_preview else "review",
            (
                ReportField(
                    "Grafična priloga",
                    "Izris iz prostorskega reda je vključen spodaj"
                    if has_map_preview
                    else "Izrisa iz prostorskega reda ni bilo mogoče samodejno pripraviti",
                ),
                ReportField("Uradni pregledovalnik", result.parcel_map.official_viewer_url if result.parcel_map else "Povezava ni bila vrnjena"),
                ReportField("Dokazila kartografskih aktov", f"{len(result.planning_land_use_map.evidence)} najdenih kartografskih listov" if result.planning_land_use_map else "Kartografsko dokazilo ni bilo pripravljeno"),
            ),
            "Za uradno prilogo uporabite grafični izsek, ki ga potrdi občina. Spletni prikaz je namenjen orientaciji in preverjanju virov.",
        ),
        ReportSection(
            10,
            "PRILOGA: PROSTORSKI IZVEDBENI POGOJI",
            "partial" if result.documents else "review",
            tuple(document_fields),
            "Izvlečki so strojno pripravljena pomoč. Pred uporabo preverite celotno uradno besedilo prostorskega akta in pogoje za konkretni poseg.",
        ),
    )


def _finding_fields(group: str, findings: list[SpatialFinding]) -> list[ReportField]:
    result: list[ReportField] = []
    for finding in findings:
        details = [finding.name]
        if finding.detail:
            details.append(finding.detail)
        if finding.reference:
            details.append(f"oznaka: {finding.reference}")
        details.append(f"vir: {finding.source}")
        result.append(ReportField(f"{group} · {finding.category}", " · ".join(details)))
    return result


def _font_path(filename: str) -> Path | None:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu") / filename,
        Path("/usr/local/share/fonts") / filename,
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _clip(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"
