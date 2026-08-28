from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from shapely.errors import GEOSException
from shapely.geometry import shape

from .config import Settings
from .gurs import GURSParcel
from .http_client import ResilientHTTPClient, wfs_params
from .models import PlanningContext


PIS_WFS_URL = "https://ipi.eprostor.gov.si/wfs-si-mnvp-pa/wfs"


@dataclass(frozen=True)
class PISAct:
    procedure_id: int
    act_id: int | None
    title: str
    act_type: str | None
    status: str | None
    page_url: str
    preparation_state: str


def _ring_wkt(ring: list[list[float]]) -> str:
    return ",".join(f"{float(x):.3f} {float(y):.3f}" for x, y, *_ in ring)


def geometry_to_wkt(geometry: dict[str, Any], bbox: list[float]) -> str:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon" and coordinates:
        rings = [f"({_ring_wkt(ring)})" for ring in coordinates]
        wkt = f"POLYGON({','.join(rings)})"
    elif geometry_type == "MultiPolygon" and coordinates:
        polygons = []
        for polygon in coordinates:
            rings = [f"({_ring_wkt(ring)})" for ring in polygon]
            polygons.append(f"({','.join(rings)})")
        wkt = f"MULTIPOLYGON({','.join(polygons)})"
    else:
        wkt = ""
    if wkt and len(wkt) <= 7_000:
        return wkt
    min_x, min_y, max_x, max_y = bbox
    return (
        "POLYGON(("
        f"{min_x} {min_y},{max_x} {min_y},{max_x} {max_y},"
        f"{min_x} {max_y},{min_x} {min_y}"
        "))"
    )


class PISClient:
    def __init__(
        self, settings: Settings, *, http_client: httpx.Client | None = None
    ) -> None:
        self.http = ResilientHTTPClient(
            settings.http_timeout_seconds, client=http_client
        )

    def close(self) -> None:
        self.http.close()

    def find_acts(self, parcel: GURSParcel) -> list[PISAct]:
        spatial_filter = self._spatial_filter(parcel)
        completed = self._features(
            "SI.MNVP.PA:OBM_PA_ZAKLJUCENI",
            spatial_filter,
            "URL,PO_EPL_ID,NAZIV_AKTA,ID_PA,STATUS,VRSTAPA_OP",
        )
        preparing = self._features(
            "SI.MNVP.PA:OBM_PA_V_PRIPRAVI",
            spatial_filter,
            "URL,PO_EPL_ID,NAZIV_PA,ID_PA,STATUS,VRSTAPA_OP",
        )
        acts: dict[int, PISAct] = {}
        for feature, state in [
            *((item, "completed") for item in completed),
            *((item, "in preparation") for item in preparing),
        ]:
            p = feature.get("properties") or {}
            procedure_id = p.get("PO_EPL_ID")
            if procedure_id is None:
                continue
            procedure_id = int(procedure_id)
            page_url = p.get("URL") or (
                "https://pis.eprostor.gov.si/pis-evt-web/pages/javni-del/"
                "prostorskiakti/prostorski_akt_podrobnosti.xhtml"
                f"?idPostopka={procedure_id}&veljavenAkt=false"
            )
            candidate = PISAct(
                procedure_id=procedure_id,
                act_id=int(p["ID_PA"]) if p.get("ID_PA") is not None else None,
                title=p.get("NAZIV_AKTA")
                or p.get("NAZIV_PA")
                or f"PIS act {procedure_id}",
                act_type=p.get("VRSTAPA_OP"),
                status=p.get("STATUS"),
                page_url=page_url,
                preparation_state=state,
            )
            previous = acts.get(procedure_id)
            if previous is None or state == "completed":
                acts[procedure_id] = candidate
        return sorted(acts.values(), key=lambda act: (act.preparation_state, act.title))

    def planning_context(self, parcel: GURSParcel) -> list[PlanningContext]:
        spatial_filter = self._spatial_filter(parcel)
        features = self._features(
            "SI.MNVP.PA:NRP_OPN",
            spatial_filter,
            "PO_EPL_ID,ID_PA,NAZIV_PA,NRP_OPIS,NRP_OZN,EUP_OZN,PEUP_OZN,GEOM",
        )
        contexts: list[PlanningContext] = []
        seen: set[tuple[Any, ...]] = set()
        parcel_shape = shape(parcel.geometry)
        for feature in features:
            p = feature.get("properties") or {}
            parcel_share_percent: float | None = None
            feature_geometry = feature.get("geometry")
            if feature_geometry:
                try:
                    overlap_area = parcel_shape.intersection(shape(feature_geometry)).area
                    if overlap_area <= 0.01:
                        continue
                    if parcel_shape.area > 0:
                        parcel_share_percent = round(
                            100 * overlap_area / parcel_shape.area, 1
                        )
                except (GEOSException, TypeError, ValueError):
                    parcel_share_percent = None
            key = (
                p.get("PO_EPL_ID"),
                p.get("NRP_OZN"),
                p.get("EUP_OZN"),
                p.get("PEUP_OZN"),
            )
            if key in seen:
                continue
            seen.add(key)
            contexts.append(
                PlanningContext(
                    act_id=int(p["PO_EPL_ID"]) if p.get("PO_EPL_ID") else None,
                    act_title=p.get("NAZIV_PA"),
                    land_use_code=self._clean(p.get("NRP_OZN")),
                    land_use_description=self._clean(p.get("NRP_OPIS")),
                    planning_unit=self._clean(p.get("EUP_OZN")),
                    subunit=self._clean(p.get("PEUP_OZN")),
                    parcel_share_percent=parcel_share_percent,
                )
            )
        return contexts

    def _features(
        self, layer: str, spatial_filter: str, properties: str
    ) -> list[dict[str, Any]]:
        payload = self.http.get_json(
            PIS_WFS_URL,
            wfs_params(layer, cql_filter=spatial_filter, properties=properties),
        )
        return payload.get("features") or []

    @staticmethod
    def _spatial_filter(parcel: GURSParcel) -> str:
        return f"INTERSECTS(GEOM,{geometry_to_wkt(parcel.geometry, parcel.bbox)})"

    @staticmethod
    def _clean(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value
