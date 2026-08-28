from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlencode

import numpy as np
import pymupdf as fitz
from pypdf import PdfReader
from shapely.errors import GEOSException
from shapely.geometry import Polygon, shape

from .gurs import GURSParcel
from .models import PlanningLandUseMap, PlanningMapEvidence


PIS_WMS_URL = "https://ipi.eprostor.gov.si/wms-si-mnvp-pa/wms"
KN_WMS_URL = "https://ipi.eprostor.gov.si/wms-si-gurs-kn/wms"
NRP_LAYER = "SI.MNVP.PA:NRP_OPN"


@dataclass(frozen=True)
class GeoPDFViewport:
    page: int
    bbox: tuple[float, float, float, float]
    geographic_points: tuple[float, ...]
    local_points: tuple[float, ...]


@dataclass(frozen=True)
class MapDocumentMatch:
    page: int
    match_method: str
    viewport: GeoPDFViewport | None = None


def is_land_use_map(source_name: str) -> bool:
    normalized = source_name.replace("\\", "/").casefold()
    filename = PurePosixPath(normalized).name
    if not normalized.endswith(".pdf"):
        return False
    if any(
        token in filename for token in ("legenda", "pregledna_karta", "pregled_karta")
    ):
        return False
    if "/eup_gji" in normalized:
        return False
    return any(
        token in normalized
        for token in (
            "/eup_nrp",
            "namensk",
            "nam_raba",
            "/graficni_prikazi/",
            "/grafični_prikazi/",
        )
    )


def inspect_map_document(
    path: Path,
    source_name: str,
    parcel: GURSParcel,
    mention_pages: list[int],
) -> list[MapDocumentMatch]:
    if not is_land_use_map(source_name):
        return []

    matches: list[MapDocumentMatch] = []
    parcel_shape = None
    if parcel.geometry_wgs84:
        try:
            parcel_shape = shape(parcel.geometry_wgs84)
        except (GEOSException, TypeError, ValueError):
            parcel_shape = None

    if parcel_shape is not None and not parcel_shape.is_empty:
        for viewport in geopdf_viewports(path):
            points = _geographic_polygon(viewport.geographic_points)
            if points is None:
                continue
            try:
                coverage = Polygon(points)
                if not coverage.is_valid:
                    coverage = coverage.buffer(0)
                if coverage.buffer(1e-7).intersects(parcel_shape):
                    matches.append(
                        MapDocumentMatch(
                            page=viewport.page,
                            match_method="geospatial",
                            viewport=viewport,
                        )
                    )
            except (GEOSException, TypeError, ValueError):
                continue

    matched_pages = {match.page for match in matches}
    for page in mention_pages:
        if page > 0 and page not in matched_pages:
            matches.append(MapDocumentMatch(page=page, match_method="parcel_label"))
    return matches


def geopdf_viewports(path: Path) -> list[GeoPDFViewport]:
    try:
        reader = PdfReader(path, strict=False)
    except Exception:
        return []

    result: list[GeoPDFViewport] = []
    for page_number, page in enumerate(reader.pages, start=1):
        raw_viewports = page.get("/VP")
        if not raw_viewports:
            continue
        try:
            viewports = list(raw_viewports)
        except TypeError:
            viewports = [raw_viewports]
        for raw_viewport in viewports:
            try:
                viewport = raw_viewport.get_object()
                measure_ref = viewport.get("/Measure")
                measure = measure_ref.get_object() if measure_ref else None
                bbox = tuple(float(value) for value in viewport.get("/BBox", []))
                geographic = tuple(float(value) for value in measure.get("/GPTS", []))
                local = tuple(float(value) for value in measure.get("/LPTS", []))
            except (AttributeError, TypeError, ValueError):
                continue
            if len(bbox) != 4 or len(geographic) < 8 or len(geographic) % 2:
                continue
            if len(local) != len(geographic):
                local = (0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0)
            result.append(
                GeoPDFViewport(
                    page=page_number,
                    bbox=bbox,
                    geographic_points=geographic,
                    local_points=local,
                )
            )
    return result


def render_pdf_map_preview(
    *,
    path: Path,
    checksum: str,
    parcel: GURSParcel,
    parcel_number: str,
    match: MapDocumentMatch,
    destination: Path,
) -> str | None:
    destination.mkdir(parents=True, exist_ok=True)
    parcel_cache_key = hashlib.sha256(
        (parcel.eid or parcel_number).encode("utf-8")
    ).hexdigest()[:12]
    filename = f"{checksum[:16]}-{parcel_cache_key}-p{match.page}-v2.png"
    output = destination / filename
    if output.is_file():
        return filename

    document: fitz.Document | None = None
    try:
        document = fitz.open(path)
        if match.page < 1 or match.page > document.page_count:
            return None
        page = document[match.page - 1]
        clip = page.rect
        if match.viewport:
            viewport_clip = _viewport_clip(page, match.viewport.bbox)
            clip = viewport_clip
            if parcel.geometry_wgs84:
                parcel_clip = _draw_parcel_outline(
                    page, match.viewport, parcel.geometry_wgs84
                )
                if parcel_clip:
                    clip = _parcel_context_clip(viewport_clip, parcel_clip, 1200 / 760)
        else:
            _highlight_parcel_label(page, parcel_number)

        scale = min(2.0, max(0.12, 1800 / max(clip.width, 1)))
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            clip=clip,
            alpha=False,
        )
        temporary = output.with_name(f".{output.stem}.part.png")
        pixmap.save(temporary)
        os.replace(temporary, output)
        return filename
    except (OSError, RuntimeError, ValueError):
        output.with_name(f".{output.stem}.part.png").unlink(missing_ok=True)
        return None
    finally:
        if document is not None:
            document.close()


def build_planning_land_use_map(
    parcel: GURSParcel, evidence: list[PlanningMapEvidence]
) -> PlanningLandUseMap:
    bbox = _map_bbox(parcel.bbox, 1200 / 760)
    common = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "SRS": "EPSG:3794",
        "BBOX": ",".join(f"{value:.2f}" for value in bbox),
        "WIDTH": 1200,
        "HEIGHT": 760,
        "STYLES": "",
    }
    land_use_url = f"{PIS_WMS_URL}?{urlencode({
        **common,
        'LAYERS': NRP_LAYER,
        'FORMAT': 'image/png',
        'TRANSPARENT': 'FALSE',
    })}"
    reference = parcel.information
    number = reference.parcel_number.replace("'", "''")
    parcel_overlay_url = f"{KN_WMS_URL}?{urlencode({
        **common,
        'LAYERS': 'SI.GURS.KN:PARCELE',
        'FORMAT': 'image/png',
        'TRANSPARENT': 'TRUE',
        'CQL_FILTER': (
            f"KO_ID={reference.cadastral_municipality_id} "
            f"AND ST_PARCELE='{number}'"
        ),
    })}"
    legend_url = f"{PIS_WMS_URL}?{urlencode({
        'SERVICE': 'WMS',
        'VERSION': '1.1.1',
        'REQUEST': 'GetLegendGraphic',
        'FORMAT': 'image/png',
        'LAYER': NRP_LAYER,
        'STYLE': 'eplan_nam_raba_skupna_opacity',
    })}"
    return PlanningLandUseMap(
        land_use_url=land_use_url,
        parcel_overlay_url=parcel_overlay_url,
        legend_url=legend_url,
        source_url=(f"{PIS_WMS_URL}?SERVICE=WMS&REQUEST=GetCapabilities"),
        note=(
            "Barvni prikaz je iz uradnega sloja PIS NRP_OPN. Rdeči katastrski obris "
            "označuje iskano parcelo. Deleži so izračunani iz geometrijskega preseka, "
            "ne iz ugibanja barve slike."
        ),
        evidence=evidence,
    )


def _geographic_polygon(values: tuple[float, ...]) -> list[tuple[float, float]] | None:
    if len(values) < 8 or len(values) % 2:
        return None
    # GeoPDF GPTS stores latitude followed by longitude.
    return [(values[index + 1], values[index]) for index in range(0, len(values), 2)]


def _viewport_clip(
    page: fitz.Page, bbox: tuple[float, float, float, float]
) -> fitz.Rect:
    x0, pdf_y0, x1, pdf_y1 = bbox
    y0 = page.rect.height - pdf_y0
    y1 = page.rect.height - pdf_y1
    clip = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    padding = min(clip.width, clip.height) * 0.015
    clip = fitz.Rect(
        clip.x0 - padding,
        clip.y0 - padding,
        clip.x1 + padding,
        clip.y1 + padding,
    )
    return clip & page.rect


def _draw_parcel_outline(
    page: fitz.Page,
    viewport: GeoPDFViewport,
    geometry: dict[str, Any],
) -> fitz.Rect | None:
    transformer = _viewport_transform(viewport)
    if transformer is None:
        return None
    line_width = max(2.0, page.rect.width / 700)
    all_points: list[fitz.Point] = []
    for ring in _geometry_rings(geometry):
        points = [
            _page_point(page, viewport, transformer, float(lon), float(lat))
            for lon, lat, *_ in ring
        ]
        if len(points) < 3:
            continue
        all_points.extend(points)
        white = page.new_shape()
        white.draw_polyline(points)
        white.finish(color=(1, 1, 1), width=line_width + 3, closePath=True)
        white.commit(overlay=True)
        red = page.new_shape()
        red.draw_polyline(points)
        red.finish(color=(0.88, 0.08, 0.08), width=line_width, closePath=True)
        red.commit(overlay=True)
    if not all_points:
        return None
    return fitz.Rect(
        min(point.x for point in all_points),
        min(point.y for point in all_points),
        max(point.x for point in all_points),
        max(point.y for point in all_points),
    )


def _parcel_context_clip(
    viewport: fitz.Rect, parcel: fitz.Rect, target_ratio: float
) -> fitz.Rect:
    width = max(parcel.width * 6, viewport.width * 0.3)
    height = max(parcel.height * 6, viewport.height * 0.3)
    if width / height < target_ratio:
        width = height * target_ratio
    else:
        height = width / target_ratio
    width = min(width, viewport.width)
    height = min(height, viewport.height)
    center = fitz.Point(
        (parcel.x0 + parcel.x1) / 2,
        (parcel.y0 + parcel.y1) / 2,
    )
    x0 = max(viewport.x0, min(center.x - width / 2, viewport.x1 - width))
    y0 = max(viewport.y0, min(center.y - height / 2, viewport.y1 - height))
    return fitz.Rect(x0, y0, x0 + width, y0 + height)


def _viewport_transform(
    viewport: GeoPDFViewport,
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]] | None:
    geographic = viewport.geographic_points
    local = viewport.local_points
    if len(geographic) != len(local) or len(geographic) < 6:
        return None
    rows = []
    local_x = []
    local_y = []
    for index in range(0, len(geographic), 2):
        latitude = geographic[index]
        longitude = geographic[index + 1]
        rows.append([longitude, latitude, 1.0])
        local_x.append(local[index])
        local_y.append(local[index + 1])
    try:
        design = np.asarray(rows, dtype=float)
        transform_x = np.linalg.lstsq(design, np.asarray(local_x), rcond=None)[0]
        transform_y = np.linalg.lstsq(design, np.asarray(local_y), rcond=None)[0]
        return transform_x, transform_y
    except np.linalg.LinAlgError:
        return None


def _page_point(
    page: fitz.Page,
    viewport: GeoPDFViewport,
    transformer: tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]],
    longitude: float,
    latitude: float,
) -> fitz.Point:
    row = np.asarray([longitude, latitude, 1.0])
    local_x = float(row @ transformer[0])
    local_y = float(row @ transformer[1])
    x0, y0, x1, y1 = viewport.bbox
    pdf_x = x0 + local_x * (x1 - x0)
    pdf_y = y0 + local_y * (y1 - y0)
    return fitz.Point(pdf_x, page.rect.height - pdf_y)


def _geometry_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        return [ring for ring in coordinates if ring]
    if geometry.get("type") == "MultiPolygon":
        return [ring for polygon in coordinates for ring in polygon if ring]
    return []


def _highlight_parcel_label(page: fitz.Page, parcel_number: str) -> None:
    variants = {parcel_number, re.sub(r"\s*/\s*", " / ", parcel_number)}
    for variant in variants:
        for rectangle in page.search_for(variant):
            page.draw_rect(
                rectangle + (-4, -3, 4, 3),
                color=(0.88, 0.08, 0.08),
                width=max(2.0, page.rect.width / 900),
                overlay=True,
            )


def _map_bbox(bbox: list[float], target_ratio: float) -> list[float]:
    min_x, min_y, max_x, max_y = bbox
    width = max(max_x - min_x, 1)
    height = max(max_y - min_y, 1)
    padding = max(18, max(width, height) * 0.18)
    width += 2 * padding
    height += 2 * padding
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    if width / height < target_ratio:
        width = height * target_ratio
    else:
        height = width / target_ratio
    return [
        center_x - width / 2,
        center_y - height / 2,
        center_x + width / 2,
        center_y + height / 2,
    ]
