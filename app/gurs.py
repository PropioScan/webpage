from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .errors import CheckerError, InvalidParcelReference, ParcelNotFound
from .http_client import ResilientHTTPClient, wfs_params
from .models import LandUseShare, ParcelInformation, ParcelReference, ValuationUnit


KN_WFS_URL = "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs"
EV_WFS_URL = "https://ipi.eprostor.gov.si/wfs-si-gurs-ev/wfs"
PARCEL_REFERENCE_RE = re.compile(
    r"^\s*(?P<ko>\d{1,4})\s*(?:[-: ]|\s)\s*(?P<parcel>\d+(?:\s*/\s*\d+)?)\s*$"
)


@dataclass
class GURSParcel:
    information: ParcelInformation
    geometry: dict[str, Any]
    bbox: list[float]
    eid: str
    geometry_wgs84: dict[str, Any] | None = None
    bbox_wgs84: list[float] | None = None


def parse_parcel_reference(value: str) -> ParcelReference:
    match = PARCEL_REFERENCE_RE.match(value)
    if not match:
        raise InvalidParcelReference(
            "Use the cadastral municipality ID and parcel number, for example ‘1723 123/4’."
        )
    parcel = re.sub(r"\s+", "", match.group("parcel"))
    return ParcelReference(
        cadastral_municipality_id=int(match.group("ko")), parcel_number=parcel
    )


def _escape_cql(value: str) -> str:
    return value.replace("'", "''")


class GURSClient:
    def __init__(
        self, settings: Settings, *, http_client: httpx.Client | None = None
    ) -> None:
        self.http = ResilientHTTPClient(
            settings.http_timeout_seconds, client=http_client
        )

    def close(self) -> None:
        self.http.close()

    def get_parcel(self, reference: ParcelReference) -> GURSParcel:
        escaped_number = _escape_cql(reference.parcel_number)
        cadastral_filter = (
            f"KO_ID={reference.cadastral_municipality_id} "
            f"AND ST_PARCELE='{escaped_number}'"
        )
        kn_payload = self.http.get_json(
            KN_WFS_URL,
            wfs_params("SI.GURS.KN:PARCELE", cql_filter=cadastral_filter),
        )
        features = kn_payload.get("features") or []
        if not features:
            raise ParcelNotFound(
                f"GURS did not find parcel {reference.canonical}. Check both identifiers."
            )
        feature = features[0]
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            raise ParcelNotFound("GURS found the parcel record but no parcel geometry.")

        geometry_wgs84: dict[str, Any] | None = None
        bbox_wgs84: list[float] | None = None
        try:
            wgs_payload = self.http.get_json(
                KN_WFS_URL,
                wfs_params(
                    "SI.GURS.KN:PARCELE",
                    cql_filter=cadastral_filter,
                    properties="GEOM",
                    srs_name="EPSG:4326",
                ),
            )
            wgs_features = wgs_payload.get("features") or []
            if wgs_features:
                geometry_wgs84 = wgs_features[0].get("geometry")
                raw_bbox = wgs_features[0].get("bbox") or wgs_payload.get("bbox")
                if raw_bbox:
                    bbox_wgs84 = [float(value) for value in raw_bbox]
        except CheckerError:
            # The projected geometry remains sufficient for the core analysis.
            # WGS84 is used only to locate the matching GeoPDF map sheet.
            pass

        eid = str(properties.get("EID_PARCELA") or properties.get("EID") or "")
        valuation_record = self._first_feature(
            "SI.GURS.EV:PARCELA",
            (
                f"KO_SIFKO={reference.cadastral_municipality_id} "
                f"AND PARCELA='{escaped_number}'"
            ),
        )
        valuation_properties = (
            valuation_record.get("properties", {}) if valuation_record else {}
        )
        valuation_eid = str(valuation_properties.get("EID_PARCELA") or eid)
        units = self._valuation_units(valuation_eid)
        land_use = self._land_use(valuation_eid)
        unit_values = [
            item.generalized_value_eur
            for item in units
            if item.generalized_value_eur is not None
        ]
        total_value = sum(unit_values) if unit_values else None

        timestamp = properties.get("DATUM_SYS")
        information = ParcelInformation(
            parcel_number=reference.parcel_number,
            cadastral_municipality_id=reference.cadastral_municipality_id,
            cadastral_municipality=properties.get("NAZIV"),
            municipality=valuation_properties.get("RPE_OBCINE_NAZIV"),
            area_m2=properties.get("POVRSINA") or valuation_properties.get("POVRSINA"),
            official_valuation_eur=total_value,
            valuation_units=units,
            land_use=land_use,
            administrative_status=properties.get("UPRAVNI_STATUSI_NAZIV_SL"),
            area_determination_method=properties.get(
                "METODE_DOLOCITVE_POVRSINE_NAZIV_SL"
            ),
            quality_score=properties.get("BONITETA"),
            cadastral_income_eur=properties.get("ZNESEK_KD"),
            building_parcel=properties.get("GRAD_PARC"),
            restriction_recorded=properties.get("OMEJITEV"),
            centroid_e=properties.get("E_CEN") or valuation_properties.get("E"),
            centroid_n=properties.get("N_CEN") or valuation_properties.get("N"),
            data_timestamp=timestamp,
        )
        bbox = feature.get("bbox") or kn_payload.get("bbox") or self._bbox(geometry)
        return GURSParcel(
            information=information,
            geometry=geometry,
            bbox=[float(value) for value in bbox],
            eid=eid,
            geometry_wgs84=geometry_wgs84,
            bbox_wgs84=bbox_wgs84,
        )

    def _first_feature(self, layer: str, cql_filter: str) -> dict[str, Any] | None:
        payload = self.http.get_json(
            EV_WFS_URL, wfs_params(layer, cql_filter=cql_filter)
        )
        features = payload.get("features") or []
        return features[0] if features else None

    def _valuation_units(self, eid: str) -> list[ValuationUnit]:
        if not eid:
            return []
        payload = self.http.get_json(
            EV_WFS_URL,
            wfs_params(
                "SI.GURS.EV:PARC_ENOTA",
                cql_filter=f"EID_PARCELA='{_escape_cql(eid)}'",
            ),
        )
        return [
            ValuationUnit(
                model_code=p.get("ID_MODEL"),
                model_name=p.get("NAZIV_MODEL"),
                area_share_percent=p.get("DELEZ_POVRSINE"),
                value_level=p.get("RAVEN"),
                generalized_value_eur=p.get("POSPLOSENA_VREDNOST"),
            )
            for feature in payload.get("features") or []
            if (p := feature.get("properties") or {})
        ]

    def _land_use(self, eid: str) -> list[LandUseShare]:
        if not eid:
            return []
        payload = self.http.get_json(
            EV_WFS_URL,
            wfs_params(
                "SI.GURS.EV:NRP_PARC",
                cql_filter=f"EID_PARCELA='{_escape_cql(eid)}'",
            ),
        )
        return [
            LandUseShare(
                name=p.get("NAZIV_NR_PARC") or "Unknown",
                share_percent=p.get("DELEZ"),
            )
            for feature in payload.get("features") or []
            if (p := feature.get("properties") or {})
        ]

    @staticmethod
    def _bbox(geometry: dict[str, Any]) -> list[float]:
        points: list[list[float]] = []

        def collect(value: Any) -> None:
            if (
                isinstance(value, list)
                and len(value) >= 2
                and all(isinstance(item, (int, float)) for item in value[:2])
            ):
                points.append(value)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(geometry.get("coordinates"))
        if not points:
            raise ParcelNotFound("GURS returned an invalid parcel geometry.")
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return [min(xs), min(ys), max(xs), max(ys)]
