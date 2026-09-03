from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from lxml import html
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry

from .config import Settings
from .errors import CheckerError, UpstreamServiceError
from .gurs import GURSParcel
from .http_client import ResilientHTTPClient, wfs_params
from .models import (
    AssessmentTone,
    InfrastructureStatus,
    LandUseAssessment,
    LandUseAssessmentItem,
    ParcelMap,
    PlanningContext,
    RoadAccessAssessment,
    SpatialFinding,
)


KGI_WFS_URL = "https://ipi.eprostor.gov.si/wfs-si-gurs-kgi/wfs"
KGI_WMS_URL = "https://ipi.eprostor.gov.si/wms-si-gurs-kgi/wms"
DOF_WMS_URL = "https://ipi.eprostor.gov.si/wms-si-gurs-dts/wms"
KN_WMS_URL = "https://ipi.eprostor.gov.si/wms-si-gurs-kn/wms"


@dataclass(frozen=True)
class InfrastructureSpec:
    key: str
    name: str
    layer: str


INFRASTRUCTURE_SPECS = (
    InfrastructureSpec("water", "Vodovod", "LINIJE_VODOVOD_G"),
    InfrastructureSpec("sewer", "Kanalizacija", "LINIJE_KANALIZACIJA_G"),
    InfrastructureSpec(
        "electricity", "Električna energija", "LINIJE_ELEKTRICNA_ENERGIJA_G"
    ),
    InfrastructureSpec(
        "telecom",
        "Elektronske komunikacije",
        "LINIJE_ELEKTRONSKE_KOMUNIKACIJE_G",
    ),
    InfrastructureSpec("gas", "Zemeljski plin", "LINIJE_ZEMELJSKI_PLIN_G"),
    InfrastructureSpec(
        "district_heat", "Toplotna energija", "LINIJE_TOPLOTNA_ENERGIJA_G"
    ),
)
ROAD_SPEC = InfrastructureSpec("road", "Cesta", "LINIJE_CESTE_G")

GJI_SOURCE = "GURS – Zbirni kataster gospodarske javne infrastrukture"
GJI_SOURCE_URL = (
    "https://www.e-prostor.gov.si/podrocja/"
    "gospodarska-javna-infrastruktura/zbirni-kataster-gji/"
)
RADOVLJICA_WMS_URL = "https://gis.iobcina.si/wms_vektor/radovljica"
RADOVLJICA_AIRPORT_LAYER = "Vplivno_obmocje_letalisca"


@dataclass(frozen=True)
class SpatialLayerSpec:
    group: str
    service_url: str
    layer_id: int
    category: str
    default_name: str
    tone: AssessmentTone
    name_fields: tuple[str, ...]
    detail_fields: tuple[tuple[str, str], ...] = ()
    reference_fields: tuple[str, ...] = ()
    source: str = "GeoHub Slovenija"


NATURE_URL = (
    "https://geohub.gov.si/ags/rest/services/DRSV/"
    "Zavarovana_in_varovana_obmocja_po_ZON/MapServer"
)
WATER_URL = "https://geohub.gov.si/ags/rest/services/DRSV/Varstvo_voda/MapServer"
FLOOD_URL = "https://geohub.gov.si/ags/rest/services/DRSV/IKRPN/MapServer"
FLOOD_WARNING_URL = (
    "https://geohub.gov.si/ags/rest/services/DRSV/Opozorilna_KP_new/MapServer"
)
HAZARD_URL = (
    "https://geohub.gov.si/ags/rest/services/DRSV/Ogrozena_obmocja/MapServer"
)
LANDSLIDE_URL = (
    "https://geohub.gov.si/ags/rest/services/DRSV/"
    "Terensko_preverjeni_zemeljski_in_hribinski_plazovi__GeoZS/MapServer"
)
HERITAGE_URL = "https://geohub.gov.si/ags/rest/services/MK/MK_EVRD/MapServer"


SPATIAL_LAYERS = (
    SpatialLayerSpec(
        "protected",
        NATURE_URL,
        5660,
        "Zavarovano območje lokalnega pomena",
        "Zavarovano območje",
        AssessmentTone.concern,
        ("IME_ZNAMEN", "IME"),
        (("status", "STATUS"), ("pomen", "POMEN"), ("predpis", "PREDPIS")),
        ("SITE_CODE",),
        "ARSO / GeoHub – varstvo narave",
    ),
    SpatialLayerSpec(
        "protected",
        NATURE_URL,
        5661,
        "Zavarovano območje državnega pomena",
        "Zavarovano območje",
        AssessmentTone.concern,
        ("IME_ZNAMEN", "IME"),
        (("status", "STATUS"), ("pomen", "POMEN"), ("predpis", "PREDPIS")),
        ("SITE_CODE",),
        "ARSO / GeoHub – varstvo narave",
    ),
    SpatialLayerSpec(
        "protected",
        NATURE_URL,
        5665,
        "Ekološko pomembno območje",
        "Ekološko pomembno območje",
        AssessmentTone.caution,
        ("IME",),
        reference_fields=("ID_STEV",),
        source="ARSO / GeoHub – varstvo narave",
    ),
    SpatialLayerSpec(
        "protected",
        NATURE_URL,
        5667,
        "Natura 2000",
        "Območje Natura 2000",
        AssessmentTone.caution,
        ("OBMOCJE",),
        (("skupina", "SKUPINA"),),
        ("SDF_ID",),
        "ARSO / GeoHub – Natura 2000",
    ),
    SpatialLayerSpec(
        "protected",
        NATURE_URL,
        5671,
        "Naravna vrednota – točka",
        "Naravna vrednota",
        AssessmentTone.caution,
        ("IME",),
        (("vrsta", "ZVRST"), ("pomen", "POMEN"), ("opis", "KRATKAOZNAKA")),
        ("EVIDSTEV", "IDENTSTEV"),
        "ARSO / GeoHub – naravne vrednote",
    ),
    SpatialLayerSpec(
        "protected",
        NATURE_URL,
        5672,
        "Jama",
        "Jama",
        AssessmentTone.caution,
        ("IME_JAME",),
        (("pomen", "POMEN"), ("režim vstopa", "REZIMVSTOP")),
        ("IDENTSTEV",),
        "ARSO / GeoHub – naravne vrednote",
    ),
    SpatialLayerSpec(
        "protected",
        NATURE_URL,
        5673,
        "Naravna vrednota – območje",
        "Naravna vrednota",
        AssessmentTone.caution,
        ("IME",),
        (("vrsta", "ZVRST"), ("pomen", "POMEN"), ("opis", "KRATKAOZNAKA")),
        ("EVIDSTEV", "IDENTSTEV"),
        "ARSO / GeoHub – naravne vrednote",
    ),
    SpatialLayerSpec(
        "constraint",
        WATER_URL,
        5361,
        "Vodovarstveno območje – državni predpis",
        "Vodovarstveno območje",
        AssessmentTone.concern,
        ("VVO_IME", "VODOVARSTV"),
        (("režim", "REZIM_IME"), ("kategorija", "KATEG_IME"), ("opomba", "OPOMBE")),
        ("VVO_ID",),
        "DRSV / GeoHub – varstvo voda",
    ),
    SpatialLayerSpec(
        "constraint",
        WATER_URL,
        5362,
        "Vodovarstveno območje – občinski predpis",
        "Vodovarstveno območje",
        AssessmentTone.concern,
        ("VVO_IME", "VODOVARSTV"),
        (("režim", "REZIM_IME"), ("kategorija", "KATEG_IME"), ("opomba", "OPOMBE")),
        ("VVO_ID",),
        "DRSV / GeoHub – varstvo voda",
    ),
    SpatialLayerSpec(
        "constraint",
        WATER_URL,
        5363,
        "Območje arteškega vodonosnika",
        "Arteški vodonosnik",
        AssessmentTone.caution,
        ("VVO_IME", "VODOVARSTV"),
        (("režim", "REZIM_IME"), ("opomba", "OPOMBE")),
        ("VVO_ID",),
        "DRSV / GeoHub – varstvo voda",
    ),
    SpatialLayerSpec(
        "constraint",
        WATER_URL,
        5364,
        "Nivo izvira",
        "Območje varstva izvira",
        AssessmentTone.caution,
        ("VVO_IME", "VODOVARSTV"),
        (("režim", "REZIM_IME"), ("opomba", "OPOMBE")),
        ("VVO_ID",),
        "DRSV / GeoHub – varstvo voda",
    ),
    SpatialLayerSpec(
        "risk", FLOOD_URL, 4011, "Poplavna nevarnost", "Velika poplavna nevarnost",
        AssessmentTone.concern, ("PV_IME",), (("razred", "KAT_NEV"),), source="DRSV / GeoHub – integralne karte poplavne nevarnosti"
    ),
    SpatialLayerSpec(
        "risk", FLOOD_URL, 4012, "Poplavna nevarnost", "Srednja poplavna nevarnost",
        AssessmentTone.concern, ("PS_IME",), (("razred", "KAT_NEV"),), source="DRSV / GeoHub – integralne karte poplavne nevarnosti"
    ),
    SpatialLayerSpec(
        "risk", FLOOD_URL, 4013, "Poplavna nevarnost", "Majhna poplavna nevarnost",
        AssessmentTone.caution, ("PM_IME",), (("razred", "KAT_NEV"),), source="DRSV / GeoHub – integralne karte poplavne nevarnosti"
    ),
    SpatialLayerSpec(
        "risk", FLOOD_URL, 4014, "Poplavna nevarnost", "Preostala poplavna nevarnost",
        AssessmentTone.caution, ("PP_IME",), (("razred", "KAT_NEV"),), source="DRSV / GeoHub – integralne karte poplavne nevarnosti"
    ),
    SpatialLayerSpec(
        "risk", FLOOD_WARNING_URL, 4016, "Opozorilna karta poplav", "Pogoste poplave",
        AssessmentTone.concern, ("PP_IME",), (("ocena", "OC_ZAN"),), source="DRSV / GeoHub – opozorilna karta poplav"
    ),
    SpatialLayerSpec(
        "risk", FLOOD_WARNING_URL, 4017, "Opozorilna karta poplav", "Redke poplave",
        AssessmentTone.caution, ("RP_IME",), (("ocena", "OC_ZAN"),), source="DRSV / GeoHub – opozorilna karta poplav"
    ),
    SpatialLayerSpec(
        "risk", FLOOD_WARNING_URL, 4018, "Opozorilna karta poplav", "Zelo redke / katastrofalne poplave",
        AssessmentTone.caution, ("ZR_IME",), (("ocena", "OC_ZAN"),), source="DRSV / GeoHub – opozorilna karta poplav"
    ),
    SpatialLayerSpec(
        "risk", HAZARD_URL, 4400, "Snežni plaz", "Območje nevarnosti snežnih plazov",
        AssessmentTone.concern, ("NEVARNOST",), (("opis", "OPIS_NEV"),), source="DRSV / GeoHub – ogrožena območja"
    ),
    SpatialLayerSpec(
        "risk", HAZARD_URL, 4401, "Erozija", "Erozijsko območje",
        AssessmentTone.caution, ("OPIS",), (("vsebina", "VSEBINA"), ("odlaganje plavin", "ZANESL")), source="DRSV / GeoHub – ogrožena območja"
    ),
    SpatialLayerSpec(
        "risk", HAZARD_URL, 4402, "Plazljivost", "Karta verjetnosti pojavljanja plazov",
        AssessmentTone.caution, ("OPIS_GRID",), (("razred", "GRIDCODE"),), source="DRSV / GeoHub – ogrožena območja"
    ),
    SpatialLayerSpec(
        "risk", LANDSLIDE_URL, 4403, "Evidentiran plaz", "Terensko preverjen zemeljski ali hribinski plaz",
        AssessmentTone.concern, ("PO_OPIS1",), (("leto preveritve", "LETO_TP"), ("občina", "OB_UIME")), ("ST_ID",), "GeoZS / DRSV / GeoHub – terensko preverjeni plazovi"
    ),
    SpatialLayerSpec(
        "heritage",
        HERITAGE_URL,
        0,
        "Kulturna dediščina",
        "Enota kulturne dediščine",
        AssessmentTone.caution,
        ("IME",),
        (("režim", "REZIM"), ("podrežim", "PODREZIM"), ("tip", "TIP"), ("predpis", "PREDPIS")),
        ("ESD", "EID"),
        "Ministrstvo za kulturo / eVRD",
    ),
)


@dataclass
class SiteAnalysisResult:
    infrastructure: list[InfrastructureStatus]
    road_access: RoadAccessAssessment
    protected_areas: list[SpatialFinding]
    constraints: list[SpatialFinding]
    risks: list[SpatialFinding]
    cultural_heritage: list[SpatialFinding]
    parcel_map: ParcelMap
    warnings: list[str]


class SiteAnalysisClient:
    def __init__(
        self, settings: Settings, *, http_client: httpx.Client | None = None
    ) -> None:
        self.http = ResilientHTTPClient(
            min(settings.http_timeout_seconds, 12), client=http_client, attempts=2
        )

    def close(self) -> None:
        self.http.close()

    def analyze(self, parcel: GURSParcel) -> SiteAnalysisResult:
        warnings: list[str] = []
        direct_records: list[dict[str, Any]] = []
        try:
            direct_records = self._direct_infrastructure(parcel)
        except CheckerError:
            warnings.append(
                "GURS ni vrnil podatkov o evidentirani GJI neposredno na parceli; "
                "prikazane so lahko le razdalje do posplošenih vodov."
            )

        infrastructure: list[InfrastructureStatus] = []
        nearest_results: dict[str, float | None | CheckerError] = {}
        spatial_results: dict[SpatialLayerSpec, list[SpatialFinding] | CheckerError] = {}
        gji_line_result: list[dict[str, Any]] | CheckerError
        municipal_regime_result: list[SpatialFinding] | CheckerError
        all_line_specs = (*INFRASTRUCTURE_SPECS, ROAD_SPEC)
        with ThreadPoolExecutor(max_workers=14) as executor:
            line_futures = {
                spec: executor.submit(self._nearest_line_distance, parcel, spec)
                for spec in all_line_specs
            }
            gji_line_future = executor.submit(self._query_detailed_gji_lines, parcel)
            municipal_regime_future = executor.submit(
                self._query_municipal_regimes, parcel
            )
            spatial_futures = {
                spec: executor.submit(self._query_spatial_layer, parcel, spec)
                for spec in SPATIAL_LAYERS
            }
            for spec, future in line_futures.items():
                try:
                    nearest_results[spec.key] = future.result()
                except CheckerError as exc:
                    nearest_results[spec.key] = exc
            for spec, future in spatial_futures.items():
                try:
                    spatial_results[spec] = future.result()
                except CheckerError as exc:
                    spatial_results[spec] = exc
            try:
                gji_line_result = gji_line_future.result()
            except CheckerError as exc:
                gji_line_result = exc
            try:
                municipal_regime_result = municipal_regime_future.result()
            except CheckerError as exc:
                municipal_regime_result = exc

        for spec in INFRASTRUCTURE_SPECS:
            matching = [
                record
                for record in direct_records
                if self._infrastructure_key(record) == spec.key
            ]
            distance_result = nearest_results[spec.key]
            if not isinstance(distance_result, CheckerError):
                infrastructure.append(
                    self._infrastructure_status(spec, matching, distance_result)
                )
            else:
                infrastructure.append(
                    InfrastructureStatus(
                        key=spec.key,
                        name=spec.name,
                        status="unavailable",
                        label="Preverjanje ni uspelo",
                        evidence_count=len(matching),
                        details=self._record_details(matching),
                        note=(
                            "Uradni sloj trenutno ni bil dosegljiv; stanja ni mogoče "
                            "zanesljivo oceniti."
                        ),
                    )
                )
                warnings.append(f"GJI sloja »{spec.name}« ni bilo mogoče preveriti.")

        road_result = nearest_results[ROAD_SPEC.key]
        if not isinstance(road_result, CheckerError):
            road_access = self._road_access(road_result)
        else:
            road_access = RoadAccessAssessment(
                tone=AssessmentTone.unknown,
                label="Preverjanje ni uspelo",
                physical_evidence="Uradnega cestnega sloja trenutno ni bilo mogoče prebrati.",
                legal_status="Pravna urejenost dostopa ni preverjena.",
                note=self._road_legal_note(),
            )
            warnings.append("GJI cestnega sloja ni bilo mogoče preveriti.")

        grouped: dict[str, list[SpatialFinding]] = {
            "protected": [],
            "constraint": [],
            "risk": [],
            "heritage": [],
        }
        failed_sources: set[tuple[str, str]] = set()
        for spec in SPATIAL_LAYERS:
            layer_result = spatial_results[spec]
            if not isinstance(layer_result, CheckerError):
                grouped[spec.group].extend(layer_result)
            else:
                failure_key = (spec.group, spec.service_url)
                if failure_key not in failed_sources:
                    warnings.append(
                        f"Dela uradnih slojev za »{self._group_name(spec.group)}« "
                        "ni bilo mogoče preveriti."
                    )
                    failed_sources.add(failure_key)

        if isinstance(gji_line_result, CheckerError):
            grouped["constraint"].extend(
                self._gji_regime_findings(parcel, direct_records, [])
            )
            warnings.append(
                "Podrobnega sloja GJI za izračun varovalnih pasov ni bilo mogoče "
                "preveriti; prikazani so le objekti, ki jih GURS neposredno povezuje s parcelo."
            )
        else:
            grouped["constraint"].extend(
                self._gji_regime_findings(parcel, direct_records, gji_line_result)
            )

        if isinstance(municipal_regime_result, CheckerError):
            if self._supports_municipal_regimes(parcel):
                warnings.append(
                    "Občinskega sloja vplivnega območja letališča ni bilo mogoče preveriti."
                )
        else:
            grouped["constraint"].extend(municipal_regime_result)

        if self._is_affirmative(parcel.information.restriction_recorded):
            grouped["constraint"].append(
                SpatialFinding(
                    category="Katastrska omejitev",
                    name="V katastru nepremičnin je evidentirana omejitev",
                    detail=(
                        "Polje OMEJITEV v uradnem zapisu parcele je pritrdilno. "
                        "Vrsto in pravni učinek preverite v izvornih evidencah."
                    ),
                    tone=AssessmentTone.caution,
                    source="GURS – kataster nepremičnin",
                    source_url="https://www.e-prostor.gov.si/",
                )
            )

        for findings in grouped.values():
            findings[:] = self._deduplicate_findings(findings)

        return SiteAnalysisResult(
            infrastructure=infrastructure,
            road_access=road_access,
            protected_areas=grouped["protected"],
            constraints=grouped["constraint"],
            risks=grouped["risk"],
            cultural_heritage=grouped["heritage"],
            parcel_map=build_parcel_map(parcel),
            warnings=warnings,
        )

    def _direct_infrastructure(self, parcel: GURSParcel) -> list[dict[str, Any]]:
        reference = parcel.information
        number = reference.parcel_number.replace("'", "''")
        cql = (
            f"KO_ID={reference.cadastral_municipality_id} "
            f"AND ST_PARCELE='{number}'"
        )
        records: list[dict[str, Any]] = []
        errors = 0
        suffixes = ("OBJEKTI_L_PARCELA", "OBJEKTI_T_PARCELA", "OBJEKTI_P_PARCELA")

        def fetch(suffix: str) -> list[dict[str, Any]]:
            payload = self.http.get_json(
                KGI_WFS_URL,
                wfs_params(f"SI.GURS.KGI:{suffix}", cql_filter=cql),
            )
            return [
                feature.get("properties") or {}
                for feature in payload.get("features") or []
            ]

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(fetch, suffix) for suffix in suffixes]
        for future in futures:
            try:
                records.extend(future.result())
            except CheckerError:
                errors += 1
        if errors == 3:
            raise UpstreamServiceError("GJI parcel layers are unavailable.")
        return records

    def _nearest_line_distance(
        self, parcel: GURSParcel, spec: InfrastructureSpec
    ) -> float | None:
        parcel_shape = shape(parcel.geometry)
        min_x, min_y, max_x, max_y = parcel.bbox
        search_distance = 100
        cql = (
            "BBOX(GEOM,"
            f"{min_x - search_distance},{min_y - search_distance},"
            f"{max_x + search_distance},{max_y + search_distance},'EPSG:3794')"
        )
        payload = self.http.get_json(
            KGI_WFS_URL,
            wfs_params(f"SI.GURS.KGI:{spec.layer}", cql_filter=cql),
        )
        distances: list[float] = []
        for feature in payload.get("features") or []:
            geometry = feature.get("geometry")
            if not geometry:
                continue
            try:
                distances.append(float(parcel_shape.distance(shape(geometry))))
            except (GEOSException, TypeError, ValueError):
                continue
        if not distances:
            return None
        nearest = min(distances)
        return round(nearest, 1) if nearest <= 100 else None

    def _query_detailed_gji_lines(
        self, parcel: GURSParcel
    ) -> list[dict[str, Any]]:
        min_x, min_y, max_x, max_y = parcel.bbox
        # The widest nationally prescribed band queried here is the 65 m gas
        # transmission-system band. A small margin avoids boundary rounding loss.
        search_distance = 70
        cql = (
            "BBOX(GEOM,"
            f"{min_x - search_distance},{min_y - search_distance},"
            f"{max_x + search_distance},{max_y + search_distance},'EPSG:3794') "
            "AND GJI_TEMATIKE_SIFRA IN (1100,2100,2200,2300,3100,3200,6100)"
        )
        params = wfs_params("SI.GURS.KGI:LINIJE", cql_filter=cql)
        result: list[dict[str, Any]] = []
        for start_index in (0, 300, 600):
            params["startIndex"] = start_index
            payload = self.http.get_json(KGI_WFS_URL, params)
            features = payload.get("features") or []
            result.extend(
                feature
                for feature in features
                if feature.get("properties") and feature.get("geometry")
            )
            if len(features) < 300:
                break
        return result

    def _query_municipal_regimes(
        self, parcel: GURSParcel
    ) -> list[SpatialFinding]:
        if not self._supports_municipal_regimes(parcel):
            return []
        min_x, min_y, max_x, max_y = _map_bbox(parcel.bbox, 1.0)
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "CRS": "EPSG:3794",
            "BBOX": f"{min_x},{min_y},{max_x},{max_y}",
            "WIDTH": 501,
            "HEIGHT": 501,
            "LAYERS": RADOVLJICA_AIRPORT_LAYER,
            "QUERY_LAYERS": RADOVLJICA_AIRPORT_LAYER,
            "INFO_FORMAT": "text/plain",
            "I": 250,
            "J": 250,
            "FEATURE_COUNT": 10,
            "STYLES": "",
        }
        response = self.http.request("GET", RADOVLJICA_WMS_URL, params=params)
        try:
            document = html.fromstring(response.content)
        except (TypeError, ValueError) as exc:
            raise UpstreamServiceError(
                "The municipal airport-regime response was unreadable."
            ) from exc
        label: str | None = None
        for row in document.xpath("//tr"):
            cells = [
                " ".join(cell.text_content().split())
                for cell in row.xpath("./th|./td")
            ]
            if len(cells) >= 2 and cells[0].casefold() == "label":
                label = cells[1].strip().upper()
                break
        if not label:
            return []
        return [
            SpatialFinding(
                category="Vplivno območje letališča",
                name=f"Vplivno območje letališča ALC Lesce – območje {label}",
                legal_basis=(
                    "64. člen Odloka o Prostorskem redu občine Radovljica "
                    "in Zakon o letalstvu (ZLet-1)"
                ),
                geometry_relation=(
                    f"Središčna točka parcele je v občinskem vektorskem sloju "
                    f"evidentirana v območju {label}. Mejo cone prikazuje geometrijska priloga."
                ),
                tone=AssessmentTone.concern,
                reference=f"cona {label}",
                source="Občina Radovljica / iObčina – Vplivno območje letališča",
                source_url=RADOVLJICA_WMS_URL,
            )
        ]

    @staticmethod
    def _supports_municipal_regimes(parcel: GURSParcel) -> bool:
        return (parcel.information.municipality or "").strip().casefold() == "radovljica"

    def _gji_regime_findings(
        self,
        parcel: GURSParcel,
        direct_records: list[dict[str, Any]],
        line_features: list[dict[str, Any]],
    ) -> list[SpatialFinding]:
        parcel_shape = shape(parcel.geometry)
        direct_eids = {
            str(record.get("EID_GJI"))
            for record in direct_records
            if record.get("EID_GJI") is not None
        }
        candidates: list[tuple[SpatialFinding, str]] = []
        matched_direct_eids: set[str] = set()

        for feature in line_features:
            properties = feature.get("properties") or {}
            if self._is_abandoned_gji(properties):
                continue
            geometry = feature.get("geometry")
            try:
                line_shape = shape(geometry)
                distance = float(parcel_shape.distance(line_shape))
            except (GEOSException, TypeError, ValueError):
                continue
            eid = self._gji_eid(properties)
            direct = bool(eid and eid in direct_eids) or distance <= 0.5
            profile = self._gji_regime_profile(properties)
            if profile is None:
                continue
            category, name, legal_basis, width = profile
            if not direct and (width is None or distance > width):
                continue
            if eid and eid in direct_eids:
                matched_direct_eids.add(eid)
            candidates.append(
                (
                    SpatialFinding(
                        category=category,
                        name=name,
                        detail=self._gji_detail(properties, width),
                        legal_basis=legal_basis,
                        geometry_relation=self._gji_geometry_relation(
                            direct=direct,
                            distance=distance,
                            width=width,
                            has_geometry=True,
                        ),
                        distance_m=round(distance, 1),
                        tone=AssessmentTone.concern,
                        reference=f"EID GJI {eid}" if eid else None,
                        source=GJI_SOURCE,
                        source_url=GJI_SOURCE_URL,
                    ),
                    eid or "",
                )
            )

        # Point/polygon parcel-relation records, or line records that could not be
        # joined to the detailed line layer, are still valid evidence of GJI on the
        # parcel. Their protection-band width is deliberately not invented.
        for properties in direct_records:
            eid = self._gji_eid(properties)
            if eid and eid in matched_direct_eids:
                continue
            profile = self._gji_regime_profile(properties)
            if profile is None:
                continue
            category, name, legal_basis, width = profile
            candidates.append(
                (
                    SpatialFinding(
                        category=category,
                        name=name,
                        detail=self._gji_detail(properties, width),
                        legal_basis=legal_basis,
                        geometry_relation=self._gji_geometry_relation(
                            direct=True,
                            distance=0,
                            width=width,
                            has_geometry=False,
                        ),
                        distance_m=0,
                        tone=AssessmentTone.concern,
                        reference=f"EID GJI {eid}" if eid else None,
                        source=GJI_SOURCE,
                        source_url=GJI_SOURCE_URL,
                    ),
                    eid or "",
                )
            )

        grouped: dict[tuple[str, str, str | None], list[tuple[SpatialFinding, str]]] = {}
        for finding, eid in candidates:
            grouped.setdefault(
                (finding.category, finding.name, finding.legal_basis), []
            ).append((finding, eid))

        findings: list[SpatialFinding] = []
        for entries in grouped.values():
            entries.sort(key=lambda item: item[0].distance_m or 0)
            finding = entries[0][0].model_copy(deep=True)
            eids = list(dict.fromkeys(eid for _, eid in entries if eid))
            if eids:
                shown = ", ".join(eids[:3])
                suffix = f" (+{len(eids) - 3})" if len(eids) > 3 else ""
                finding.reference = f"EID GJI {shown}{suffix}"
            findings.append(finding)
        return sorted(findings, key=lambda item: (item.category, item.name))

    @classmethod
    def _gji_regime_profile(
        cls, properties: dict[str, Any]
    ) -> tuple[str, str, str, float | None] | None:
        key = cls._infrastructure_key(properties)
        if key is None:
            return None
        kind = cls._clean_value(
            properties.get("GJI_VRSTE_OBJEKTOV_NAZIV_SL")
        ) or {
            "road": "Javna cesta",
            "electricity": "Elektroenergetski vod ali objekt",
            "telecom": "Elektronska komunikacijska infrastruktura",
            "water": "Vodovodno omrežje",
            "sewer": "Kanalizacijsko omrežje",
            "gas": "Plinovodno omrežje",
            "district_heat": "Omrežje daljinskega ogrevanja",
        }.get(key, "Gospodarska javna infrastruktura")
        attr1 = cls._clean_gji_attribute(properties.get("GJI_ATR1_NAZIV_SL"))
        attr2 = cls._clean_gji_attribute(properties.get("GJI_ATR2_NAZIV_SL"))
        combined = " ".join(part for part in (kind, attr1, attr2) if part).lower()

        if key == "electricity":
            voltage = attr2 if attr2 and "kv" in attr2.lower() else None
            underground = "podzem" in combined or "kablovod" in combined
            overhead = "nadzem" in combined or "daljnovod" in combined
            if underground:
                name = f"Podzemni kabelski vod{f' {voltage}' if voltage else ''}"
            elif overhead:
                name = f"Nadzemni elektroenergetski vod{f' {voltage}' if voltage else ''}"
            else:
                name = kind
            return (
                "Varovalni pas elektroenergetskega omrežja",
                name,
                "112. člen Energetskega zakona (EZ-2)",
                cls._electricity_band_width(combined, voltage),
            )
        if key == "telecom":
            position = f" – {attr1.lower()}" if attr1 else ""
            return (
                "Varovalni pas elektronskih komunikacij",
                f"Elektronske komunikacije – {kind.lower()}{position}",
                "17. člen Zakona o elektronskih komunikacijah (ZEKom-2)",
                3.0,
            )
        if key == "water":
            return (
                "Varovalni pas vodovodnega omrežja",
                f"Vodovod – {kind.lower()}",
                "Veljavni občinski prostorski akt in pogoji upravljavca vodovoda",
                None,
            )
        if key == "sewer":
            return (
                "Varovalni pas kanalizacijskega omrežja",
                f"Kanalizacija – {kind.lower()}",
                "Veljavni občinski prostorski akt in pogoji upravljavca kanalizacije",
                None,
            )
        if key == "road":
            road_class = attr1 or kind
            width = cls._road_band_width(road_class)
            legal_basis = (
                "76. člen Zakona o cestah (ZCes-2)"
                if width is not None
                else "Zakon o cestah (ZCes-2) in veljavni občinski odlok o občinskih cestah"
            )
            return (
                "Varovalni pas javne ceste",
                f"{road_class[:1].upper()}{road_class[1:]}",
                legal_basis,
                width,
            )
        if key == "gas":
            width = 65.0 if "prenos" in combined else 5.0 if "distrib" in combined else None
            return (
                "Varovalni pas sistema zemeljskega plina",
                kind,
                "113. člen Energetskega zakona (EZ-2)",
                width,
            )
        if key == "district_heat":
            return (
                "Varovalni pas omrežja daljinskega ogrevanja",
                kind,
                "Veljavni občinski prostorski akt in pogoji upravljavca omrežja",
                None,
            )
        return None

    @staticmethod
    def _electricity_band_width(
        combined: str, voltage: str | None
    ) -> float | None:
        if voltage is None:
            return None
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*k\s*v", voltage, re.IGNORECASE)
        if match is None:
            return None
        kv = float(match.group(1).replace(",", "."))
        underground = "podzem" in combined or "kablovod" in combined
        overhead = "nadzem" in combined or "daljnovod" in combined
        if underground:
            if kv >= 220:
                return 10.0
            if kv >= 35:
                return 3.0
            return 1.0
        if overhead:
            if kv >= 220:
                return 40.0
            if kv >= 35:
                return 15.0
            if kv > 1:
                return 10.0
            return 1.5
        return None

    @staticmethod
    def _road_band_width(road_class: str) -> float | None:
        normalized = road_class.lower()
        if "avtocest" in normalized:
            return 40.0
        if "hitra cest" in normalized:
            return 35.0
        if "glavna cest" in normalized:
            return 25.0
        if "regionalna cest" in normalized:
            return 15.0
        if "državna kolesars" in normalized:
            return 5.0
        return None

    @staticmethod
    def _gji_geometry_relation(
        *, direct: bool, distance: float, width: float | None, has_geometry: bool
    ) -> str:
        if has_geometry and distance <= 0.5:
            relation = "Evidentirana os oziroma objekt GJI seka ali se dotika parcele."
        elif has_geometry:
            relation = f"Evidentirana os GJI je približno {distance:.1f} m od parcele."
        else:
            relation = "GURS objekt GJI neposredno povezuje z iskano parcelo."
        if width is not None:
            relation += (
                f" Zakonski pas je {width:g} m na vsako stran; njegov izračunani "
                "obris seka parcelo."
            )
        elif direct:
            relation += (
                " Točno širino varovalnega pasu in pogoje posega je treba potrditi "
                "v občinskem aktu oziroma pri upravljavcu."
            )
        return relation

    @classmethod
    def _gji_detail(
        cls, properties: dict[str, Any], width: float | None
    ) -> str | None:
        parts: list[str] = []
        description = cls._clean_gji_attribute(properties.get("OPIS"))
        source_method = cls._clean_gji_attribute(
            properties.get("GJI_VIRI_NAZIV_SL")
        )
        source_date = cls._clean_gji_attribute(properties.get("DATUM_VIRA"))
        if description:
            parts.append(f"opis GJI: {description}")
        if width is not None:
            parts.append(f"širina pasu: {width:g} m na vsako stran")
        if source_method:
            suffix = f", {source_date}" if source_date else ""
            parts.append(f"podlaga zajema: {source_method}{suffix}")
        return " · ".join(parts) or None

    @staticmethod
    def _clean_gji_attribute(value: Any) -> str | None:
        cleaned = SiteAnalysisClient._clean_value(value)
        if cleaned and cleaned.casefold() not in {"ni podatka", "ni določeno"}:
            return cleaned
        return None

    @staticmethod
    def _gji_eid(properties: dict[str, Any]) -> str | None:
        value = properties.get("EID_LINIJA") or properties.get("EID_GJI")
        return str(value) if value is not None else None

    @staticmethod
    def _is_abandoned_gji(properties: dict[str, Any]) -> bool:
        value = str(properties.get("GJI_OPUSCENOSTI_NAZIV_SL") or "").casefold()
        return value.startswith("opuščeni")

    def _query_spatial_layer(
        self, parcel: GURSParcel, spec: SpatialLayerSpec
    ) -> list[SpatialFinding]:
        parcel_shape = shape(parcel.geometry)
        min_x, min_y, max_x, max_y = parcel.bbox
        params: dict[str, str | int] = {
            "where": "1=1",
            "geometry": f"{min_x},{min_y},{max_x},{max_y}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 3794,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 3794,
            "geometryPrecision": 2,
            "resultRecordCount": 200,
            "f": "json",
        }
        findings: list[SpatialFinding] = []
        offset = 0
        for _ in range(5):
            params["resultOffset"] = offset
            payload = self.http.get_json(
                f"{spec.service_url}/{spec.layer_id}/query", params
            )
            if payload.get("error"):
                raise UpstreamServiceError("GeoHub returned a layer error.")
            features = payload.get("features") or []
            for feature in features:
                geometry = feature.get("geometry")
                if not geometry:
                    continue
                try:
                    feature_shape = _spatial_shape(geometry)
                    if feature_shape is None:
                        continue
                    overlap = parcel_shape.intersection(feature_shape)
                    if overlap.is_empty:
                        continue
                    if feature_shape.geom_type in {"Polygon", "MultiPolygon"}:
                        if overlap.area <= 0.01:
                            continue
                except (GEOSException, TypeError, ValueError):
                    continue
                properties = feature.get("attributes") or feature.get("properties") or {}
                name = self._first_value(properties, spec.name_fields) or spec.default_name
                detail_parts = []
                for label, field in spec.detail_fields:
                    value = self._clean_value(properties.get(field))
                    if value:
                        detail_parts.append(f"{label}: {value}")
                reference = self._first_value(properties, spec.reference_fields)
                findings.append(
                    SpatialFinding(
                        category=spec.category,
                        name=name,
                        detail=" · ".join(detail_parts) or None,
                        legal_basis=self._spatial_legal_basis(spec, properties),
                        geometry_relation="Uradni sloj geometrijsko seka območje parcele.",
                        tone=self._finding_tone(spec, properties),
                        reference=reference,
                        source=spec.source,
                        source_url=spec.service_url,
                    )
                )
            if len(features) < 200 and not payload.get("exceededTransferLimit"):
                break
            offset += len(features)
            if not features:
                break
        return findings

    @staticmethod
    def _infrastructure_status(
        spec: InfrastructureSpec,
        matching: list[dict[str, Any]],
        distance: float | None,
    ) -> InfrastructureStatus:
        details = SiteAnalysisClient._record_details(matching)
        if matching or (distance is not None and distance <= 0.5):
            return InfrastructureStatus(
                key=spec.key,
                name=spec.name,
                status="on_parcel",
                label="Evidentirano na parceli",
                distance_m=0 if matching else distance,
                evidence_count=max(len(matching), 1),
                details=details,
                note=(
                    "Evidenca kaže objekt ali vod na parceli. To še ne potrjuje "
                    "obstoječega priključka, kapacitete ali soglasja upravljavca."
                ),
            )
        if distance is not None:
            return InfrastructureStatus(
                key=spec.key,
                name=spec.name,
                status="nearby",
                label=f"Najbližji evidentirani vod približno {distance:g} m",
                distance_m=distance,
                details=details,
                note=(
                    "Razdalja je izračunana do posplošenega uradnega sloja. "
                    "Možnost in strošek priklopa potrdi upravljavec."
                ),
            )
        return InfrastructureStatus(
            key=spec.key,
            name=spec.name,
            status="not_recorded",
            label="Ni evidentirano v pasu 100 m",
            details=details,
            note=(
                "V preverjenem uradnem sloju ni bil vrnjen vod v pasu 100 m. "
                "To ni dokaz, da infrastrukture na terenu ni."
            ),
        )

    @staticmethod
    def _road_access(distance: float | None) -> RoadAccessAssessment:
        legal_status = "Pravna urejenost dostopa ni preverjena."
        if distance is None:
            return RoadAccessAssessment(
                tone=AssessmentTone.concern,
                label="Cestni vod ni evidentiran v pasu 100 m",
                physical_evidence=(
                    "Posplošeni sloj GJI v preverjenem pasu ni vrnil cestnega voda."
                ),
                legal_status=legal_status,
                note=SiteAnalysisClient._road_legal_note(),
            )
        if distance <= 1:
            return RoadAccessAssessment(
                tone=AssessmentTone.positive,
                label="Kartografski stik s cestnim vodom",
                distance_m=distance,
                physical_evidence=(
                    "Meja parcele se po uradnem posplošenem sloju dotika ali seka "
                    "evidentirani cestni vod."
                ),
                legal_status=legal_status,
                note=SiteAnalysisClient._road_legal_note(),
            )
        if distance <= 10:
            return RoadAccessAssessment(
                tone=AssessmentTone.caution,
                label=f"Cestni vod je približno {distance:g} m od parcele",
                distance_m=distance,
                physical_evidence=(
                    "Cesta je kartografsko blizu, vendar neposredni stik s parcelo ni prikazan."
                ),
                legal_status=legal_status,
                note=SiteAnalysisClient._road_legal_note(),
            )
        return RoadAccessAssessment(
            tone=AssessmentTone.concern,
            label=f"Cestni vod je približno {distance:g} m od parcele",
            distance_m=distance,
            physical_evidence="Neposredni kartografski stik s cestnim vodom ni prikazan.",
            legal_status=legal_status,
            note=SiteAnalysisClient._road_legal_note(),
        )

    @staticmethod
    def _road_legal_note() -> str:
        return (
            "Ta rezultat kaže le geometrijo GJI, ne pa statusa javne ceste ali pravice "
            "dostopa. Lastništvo, služnost, kategorizacijo ceste in dovoljen priključek "
            "preverite v zemljiški knjigi ter pri občini oziroma upravljavcu ceste."
        )

    @staticmethod
    def _infrastructure_key(properties: dict[str, Any]) -> str | None:
        code = str(properties.get("GJI_TEMATIKE_SIFRA") or "")
        name = str(properties.get("GJI_TEMATIKE_NAZIV_SL") or "").lower()
        if code.startswith("31") or "vodovod" in name:
            return "water"
        if code.startswith("32") or "kanaliz" in name:
            return "sewer"
        if code.startswith("21") or "električ" in name:
            return "electricity"
        if code.startswith("61") or "komunik" in name:
            return "telecom"
        if code.startswith("22") or "zemeljski plin" in name:
            return "gas"
        if code.startswith("23") or "toplot" in name:
            return "district_heat"
        if code.startswith("11") or "cest" in name:
            return "road"
        return None

    @staticmethod
    def _spatial_legal_basis(
        spec: SpatialLayerSpec, properties: dict[str, Any]
    ) -> str:
        recorded = SiteAnalysisClient._clean_value(properties.get("PREDPIS"))
        if recorded:
            return recorded
        if spec.group == "protected":
            return "Zakon o ohranjanju narave (ZON) in predpis o konkretnem območju"
        if spec.group == "heritage":
            return "Zakon o varstvu kulturne dediščine (ZVKD-1) in akt o razglasitvi"
        if spec.group == "risk":
            return "Zakon o vodah (ZV-1) oziroma področni predpis za evidentirano nevarnost"
        return "Zakon o vodah (ZV-1) in predpis o konkretnem varovanem območju"

    @staticmethod
    def _record_details(records: list[dict[str, Any]]) -> list[str]:
        details = []
        for record in records:
            theme = SiteAnalysisClient._clean_value(
                record.get("GJI_TEMATIKE_NAZIV_SL")
            )
            kind = SiteAnalysisClient._clean_value(
                record.get("GJI_VRSTE_OBJEKTOV_NAZIV_SL")
            )
            detail = " – ".join(part for part in (theme, kind) if part)
            if detail and detail not in details:
                details.append(detail)
        return details[:8]

    @staticmethod
    def _finding_tone(
        spec: SpatialLayerSpec, properties: dict[str, Any]
    ) -> AssessmentTone:
        if spec.group == "heritage":
            combined = " ".join(
                str(properties.get(field) or "")
                for field in ("REZIM", "PODREZIM", "TIP", "ZVRST")
            ).lower()
            if "spomenik" in combined or "arheolo" in combined:
                return AssessmentTone.concern
        return spec.tone

    @staticmethod
    def _first_value(properties: dict[str, Any], fields: tuple[str, ...]) -> str | None:
        for field in fields:
            value = SiteAnalysisClient._clean_value(properties.get(field))
            if value:
                return value
        return None

    @staticmethod
    def _clean_value(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text if text and text.lower() not in {"null", "none", "nan"} else None

    @staticmethod
    def _deduplicate_findings(findings: list[SpatialFinding]) -> list[SpatialFinding]:
        unique: dict[tuple[str, str, str | None], SpatialFinding] = {}
        for finding in findings:
            unique[(finding.category, finding.name, finding.reference)] = finding
        return sorted(unique.values(), key=lambda item: (item.category, item.name))

    @staticmethod
    def _is_affirmative(value: str | None) -> bool:
        if value is None:
            return False
        normalized = value.strip().lower()
        return normalized in {"da", "yes", "true", "1", "y"}

    @staticmethod
    def _group_name(group: str) -> str:
        return {
            "protected": "varovana območja",
            "constraint": "vodovarstvene omejitve",
            "risk": "naravne nevarnosti",
            "heritage": "kulturno dediščino",
        }[group]


def assess_land_use(
    parcel: GURSParcel, contexts: list[PlanningContext]
) -> LandUseAssessment:
    items: list[LandUseAssessmentItem] = []
    grouped: dict[
        tuple[str | None, str], tuple[float, bool]
    ] = {}
    for context in contexts:
        code = (context.land_use_code or "").strip().upper() or None
        name = (
            context.land_use_description
            or _LAND_USE_DICTIONARY.get(code or "")
            or "Namenska raba brez opisa"
        )
        key = (code, name)
        total, has_share = grouped.get(key, (0.0, False))
        if context.parcel_share_percent is not None:
            total += context.parcel_share_percent
            has_share = True
        grouped[key] = (total, has_share)

    for (code, name), (total, has_share) in grouped.items():
        share = round(min(total, 100.0), 1) if has_share else None
        tone, label, explanation = _classify_land_use(code, name)
        items.append(
            LandUseAssessmentItem(
                code=code,
                name=name,
                parcel_share_percent=share,
                parcel_area_m2=_share_area(parcel, share),
                tone=tone,
                label=label,
                explanation=explanation,
            )
        )

    if not items:
        for recorded in parcel.information.land_use:
            tone, label, explanation = _classify_land_use(None, recorded.name)
            items.append(
                LandUseAssessmentItem(
                    name=recorded.name,
                    parcel_share_percent=recorded.share_percent,
                    parcel_area_m2=_share_area(parcel, recorded.share_percent),
                    tone=tone,
                    label=label,
                    explanation=explanation,
                )
            )

    items.sort(
        key=lambda item: (
            item.parcel_share_percent is None,
            -(item.parcel_share_percent or 0),
            item.code or "",
        )
    )

    tones = {item.tone for item in items}
    if not items or tones == {AssessmentTone.unknown}:
        overall_tone = AssessmentTone.unknown
        label = "Ni dovolj podatkov"
        summary = "Uradni strukturirani sloj ne omogoča zanesljive indikativne ocene."
    elif tones <= {AssessmentTone.positive}:
        overall_tone = AssessmentTone.positive
        label = "Ugodna indikacija za stanovanjsko gradnjo"
        summary = (
            "Namenska raba je poselitvena oziroma stanovanjska, vendar je treba "
            "preveriti še prostorske izvedbene pogoje in druga dovoljenja."
        )
    elif tones <= {AssessmentTone.concern}:
        overall_tone = AssessmentTone.concern
        label = "Omejujoča namenska raba"
        summary = (
            "Prikazana namenska raba praviloma ni namenjena običajni stanovanjski "
            "gradnji oziroma zahteva posebno pravno podlago."
        )
    else:
        overall_tone = AssessmentTone.caution
        label = "Pogojno ali mešano stanje"
        summary = (
            "Parcela ima mešano ali pogojno namensko rabo. Ocena zazidljivosti je "
            "odvisna od natančne lege objekta, EUP in občinskih izvedbenih pogojev."
        )

    return LandUseAssessment(
        tone=overall_tone,
        label=label,
        summary=summary,
        items=items,
        disclaimer=(
            "Oznaka »ugodna« je le orientacijska ocena za stanovanjsko gradnjo. "
            "Namenska raba sama ne ustvarja pravice graditi; preveriti je treba OPN/OPPN, "
            "prostorske izvedbene pogoje, minimalno parcelo, odmike, dostop, komunalno "
            "opremljenost, varovanja in mnenja pristojnih organov."
        ),
    )


_LAND_USE_DICTIONARY = {
    "S": "Območja stanovanj",
    "SS": "Stanovanjske površine",
    "SB": "Stanovanjske površine za posebne namene",
    "SK": "Površine podeželskega naselja",
    "A": "Površine razpršene poselitve",
    "K1": "Najboljša kmetijska zemljišča",
    "K2": "Druga kmetijska zemljišča",
    "G": "Gozdna zemljišča",
    "VC": "Površinske celinske vode",
    "PC": "Površine cest",
}


def _share_area(parcel: GURSParcel, share: float | None) -> float | None:
    if share is None or parcel.information.area_m2 is None:
        return None
    return round(parcel.information.area_m2 * share / 100, 1)


def _classify_land_use(
    code: str | None, name: str
) -> tuple[AssessmentTone, str, str]:
    normalized_code = (code or "").strip().upper()
    normalized_name = name.lower()
    residential_codes = {"S", "SS", "SB", "SK", "SP"}
    conditional_codes = {"A", "C", "CU", "CD", "BT"}
    restrictive_codes = {
        "K1", "K2", "G", "V", "VC", "VI", "Z", "ZD", "ZS", "ZP", "ZK",
        "I", "IP", "IG", "IK", "P", "PC", "PO", "PL", "T", "E", "O", "L", "N",
    }
    if normalized_code in residential_codes or "stanovanj" in normalized_name:
        return (
            AssessmentTone.positive,
            "Poselitvena / stanovanjska raba",
            "Raba je načeloma združljiva s stanovanjsko gradnjo, ob izpolnitvi izvedbenih pogojev.",
        )
    if normalized_code == "A" or "razpršene poselitve" in normalized_name:
        return (
            AssessmentTone.caution,
            "Razpršena poselitev – preverite pogoje",
            "Gradnja je lahko dopustna le v obsegu in pod pogoji, ki jih določa občinski prostorski akt.",
        )
    if normalized_code in conditional_codes or "centralnih dejavnosti" in normalized_name:
        return (
            AssessmentTone.caution,
            "Pogojna združljivost",
            "Dopustnost stanovanja je odvisna od podrobnejše namenske rabe in izvedbenih pogojev.",
        )
    if normalized_code in restrictive_codes or any(
        term in normalized_name
        for term in ("kmetij", "gozd", "vodn", "zelene površine", "promet")
    ):
        return (
            AssessmentTone.concern,
            "Praviloma omejujoče za stanovanjsko gradnjo",
            "Ta raba praviloma ni običajno stavbno zemljišče za novo stanovanjsko gradnjo.",
        )
    return (
        AssessmentTone.unknown,
        "Potrebna je razlaga občine",
        "Kode ni mogoče varno razvrstiti brez podrobnejšega prostorskega akta.",
    )


def build_parcel_map(parcel: GURSParcel) -> ParcelMap:
    bbox = _map_bbox(parcel.bbox, 1200 / 760)
    bbox_text = ",".join(f"{value:.2f}" for value in bbox)
    common = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "SRS": "EPSG:3794",
        "BBOX": bbox_text,
        "WIDTH": 1200,
        "HEIGHT": 760,
        "STYLES": "",
    }
    orthophoto_params = {
        **common,
        "LAYERS": "SI.GURS.ZPDZ:DOF025",
        "FORMAT": "image/jpeg",
    }
    orthophoto_url = f"{DOF_WMS_URL}?{urlencode(orthophoto_params)}"
    reference = parcel.information
    number = reference.parcel_number.replace("'", "''")
    overlay_params = {
        **common,
        "LAYERS": "SI.GURS.KN:PARCELE",
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
        "CQL_FILTER": (
            f"KO_ID={reference.cadastral_municipality_id} "
            f"AND ST_PARCELE='{number}'"
        ),
    }
    overlay_url = f"{KN_WMS_URL}?{urlencode(overlay_params)}"
    ordered_boundary_params = {
        **common,
        "LAYERS": "SI.GURS.KN:UREJENE_MEJE",
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
    }
    ordered_boundary_url = (
        f"{KN_WMS_URL}?{urlencode(ordered_boundary_params)}"
    )
    infrastructure_params = {
        **common,
        "LAYERS": ",".join(
            f"SI.GURS.KGI:{spec.layer}" for spec in INFRASTRUCTURE_SPECS
        ),
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
    }
    infrastructure_url = f"{KGI_WMS_URL}?{urlencode(infrastructure_params)}"
    legal_regime_params = {
        **common,
        "LAYERS": ",".join(
            [
                *(f"SI.GURS.KGI:{spec.layer}" for spec in INFRASTRUCTURE_SPECS),
                f"SI.GURS.KGI:{ROAD_SPEC.layer}",
                "SI.GURS.KGI:LINIJE_LETALISCA_G",
                "SI.GURS.KGI:POLIGONI_LETALISCA_G",
            ]
        ),
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
    }
    legal_regime_url = f"{KGI_WMS_URL}?{urlencode(legal_regime_params)}"
    additional_regime_urls: list[str] = []
    if SiteAnalysisClient._supports_municipal_regimes(parcel):
        airport_params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "CRS": "EPSG:3794",
            "BBOX": bbox_text,
            "WIDTH": 1200,
            "HEIGHT": 760,
            "LAYERS": RADOVLJICA_AIRPORT_LAYER,
            "STYLES": "",
            "FORMAT": "image/png",
            "TRANSPARENT": "TRUE",
        }
        additional_regime_urls.append(
            f"{RADOVLJICA_WMS_URL}?{urlencode(airport_params)}"
        )
    return ParcelMap(
        orthophoto_url=orthophoto_url,
        parcel_overlay_url=overlay_url,
        ordered_boundary_overlay_url=ordered_boundary_url,
        infrastructure_overlay_url=infrastructure_url,
        legal_regime_overlay_url=legal_regime_url,
        legal_regime_additional_overlay_urls=additional_regime_urls,
        official_viewer_url=(
            "https://ipi.eprostor.gov.si/jv/"
            + (f"?eid={parcel.eid}" if parcel.eid else "")
        ),
        note=(
            "Ortofoto in katastrski prikaz imata lahko različna datuma zajema. "
            "Prikaz meje ni geodetska zakoličba in ne nadomešča ureditve meje na terenu. "
            "Poudarjene mejne daljice prikazuje uradni sloj GURS Urejene meje. "
            "Evidentirani komunalni vodi (GJI) so posplošeni in informativni; lega na "
            "karti ne pomeni priključka ali soglasja upravljavca. Geometrijska priloga "
            "pravnih režimov prikazuje evidentirane osi in objekte, ne pa nujno uradnega "
            "obrisa njihovega varovalnega pasu."
        ),
    )


def _spatial_shape(geometry: dict[str, Any]) -> BaseGeometry | None:
    """Read either GeoJSON or ArcGIS JSON while preserving EPSG:3794 coordinates."""
    if geometry.get("type"):
        return shape(geometry)
    if geometry.get("x") is not None and geometry.get("y") is not None:
        return Point(float(geometry["x"]), float(geometry["y"]))
    if geometry.get("points"):
        return MultiPoint(geometry["points"])
    if geometry.get("paths"):
        paths = geometry["paths"]
        if len(paths) == 1:
            return LineString(paths[0])
        return MultiLineString(paths)
    if geometry.get("rings"):
        result: BaseGeometry | None = None
        for ring in geometry["rings"]:
            if len(ring) < 4:
                continue
            polygon = Polygon(ring)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            result = polygon if result is None else result.symmetric_difference(polygon)
        return result
    return None


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
