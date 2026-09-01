from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ParcelReference(BaseModel):
    cadastral_municipality_id: int
    parcel_number: str

    @property
    def canonical(self) -> str:
        return f"{self.cadastral_municipality_id} {self.parcel_number}"


class ValuationUnit(BaseModel):
    model_code: str | None = None
    model_name: str | None = None
    area_share_percent: float | None = None
    value_level: str | None = None
    generalized_value_eur: int | None = None


class LandUseShare(BaseModel):
    name: str
    share_percent: float | None = None


class ParcelInformation(BaseModel):
    parcel_number: str
    cadastral_municipality_id: int
    cadastral_municipality: str | None = None
    municipality: str | None = None
    area_m2: int | None = None
    official_valuation_eur: int | None = None
    valuation_units: list[ValuationUnit] = Field(default_factory=list)
    land_use: list[LandUseShare] = Field(default_factory=list)
    administrative_status: str | None = None
    area_determination_method: str | None = None
    quality_score: int | None = None
    cadastral_income_eur: float | None = None
    building_parcel: str | None = None
    restriction_recorded: str | None = None
    centroid_e: float | None = None
    centroid_n: float | None = None
    data_timestamp: str | None = None
    source: str = "Geodetska uprava Republike Slovenije (GURS)"
    source_url: str = "https://www.e-prostor.gov.si/"
    valuation_source_url: str = "https://vrednotenje.gov.si/EV_JV"


class PlanningContext(BaseModel):
    act_id: int | None = None
    act_title: str | None = None
    land_use_code: str | None = None
    land_use_description: str | None = None
    planning_unit: str | None = None
    subunit: str | None = None
    parcel_share_percent: float | None = None


class AssessmentTone(str, Enum):
    positive = "positive"
    caution = "caution"
    concern = "concern"
    neutral = "neutral"
    unknown = "unknown"


class LandUseAssessmentItem(BaseModel):
    code: str | None = None
    name: str
    parcel_share_percent: float | None = None
    parcel_area_m2: float | None = None
    tone: AssessmentTone
    label: str
    explanation: str


class LandUseAssessment(BaseModel):
    tone: AssessmentTone
    label: str
    summary: str
    items: list[LandUseAssessmentItem] = Field(default_factory=list)
    disclaimer: str


class PlanningActResult(BaseModel):
    procedure_id: int
    act_id: int | None = None
    title: str
    act_type: str | None = None
    status: str | None = None
    preparation_state: str
    page_url: str
    document_count: int = 0
    literal_mention_count: int = 0


class InfrastructureStatus(BaseModel):
    key: str
    name: str
    status: str
    label: str
    distance_m: float | None = None
    evidence_count: int = 0
    details: list[str] = Field(default_factory=list)
    note: str
    source: str = "GURS Zbirni kataster gospodarske javne infrastrukture"
    source_url: str = "https://www.e-prostor.gov.si/podrocja/gospodarska-javna-infrastruktura/zbirni-kataster-gji/"


class RoadAccessAssessment(BaseModel):
    tone: AssessmentTone
    label: str
    distance_m: float | None = None
    physical_evidence: str
    legal_status: str
    note: str
    source_url: str = "https://www.e-prostor.gov.si/podrocja/gospodarska-javna-infrastruktura/zbirni-kataster-gji/"


class SpatialFinding(BaseModel):
    category: str
    name: str
    detail: str | None = None
    tone: AssessmentTone = AssessmentTone.caution
    reference: str | None = None
    source: str
    source_url: str


class ParcelMap(BaseModel):
    orthophoto_url: str
    parcel_overlay_url: str
    infrastructure_overlay_url: str | None = None
    official_viewer_url: str
    source: str = "GURS ortofoto in kataster nepremičnin"
    source_url: str = "https://www.e-prostor.gov.si/dostopi/javni-dostop/"
    note: str


class PlanningMapEvidence(BaseModel):
    act_id: int
    act_title: str
    pdf_title: str
    pdf_download_url: str
    preview_url: str | None = None
    page: int = 1
    match_method: str
    source_path: str | None = None


class PlanningLandUseMap(BaseModel):
    land_use_url: str
    parcel_overlay_url: str
    legend_url: str
    source: str = "PIS – namenska raba prostora (OPN)"
    source_url: str = "https://pis.eprostor.gov.si/"
    dictionary_source: str = "PIS NRP_OPN: NRP_OZN in NRP_OPIS"
    note: str
    evidence: list[PlanningMapEvidence] = Field(default_factory=list)


class Excerpt(BaseModel):
    page: int
    section: str | None = None
    text: str


class Importance(str, Enum):
    high = "high"
    medium = "medium"
    info = "info"


class ImportantFinding(BaseModel):
    category: str
    detail: str
    importance: Importance = Importance.info
    pages: list[int] = Field(default_factory=list)


class OpenAIUsage(BaseModel):
    configured: bool = False
    model: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    rate_limit_tokens: int | None = None
    rate_limit_remaining_tokens: int | None = None
    rate_limit_reset: str | None = None


class DocumentResult(BaseModel):
    act_id: int
    act_title: str
    act_type: str | None = None
    act_status: str | None = None
    act_page_url: str
    pdf_title: str
    pdf_download_url: str
    source_path: str | None = None
    size_bytes: int
    sha256: str
    mention_count: int
    excerpts: list[Excerpt] = Field(default_factory=list)
    summary: str
    summary_provider: str
    important_findings: list[ImportantFinding] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    parcel: ParcelInformation
    planning_context: list[PlanningContext] = Field(default_factory=list)
    land_use_assessment: LandUseAssessment | None = None
    planning_acts: list[PlanningActResult] = Field(default_factory=list)
    infrastructure: list[InfrastructureStatus] = Field(default_factory=list)
    road_access: RoadAccessAssessment | None = None
    protected_areas: list[SpatialFinding] = Field(default_factory=list)
    constraints: list[SpatialFinding] = Field(default_factory=list)
    risks: list[SpatialFinding] = Field(default_factory=list)
    cultural_heritage: list[SpatialFinding] = Field(default_factory=list)
    parcel_map: ParcelMap | None = None
    planning_land_use_map: PlanningLandUseMap | None = None
    documents: list[DocumentResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    openai_usage: OpenAIUsage = Field(default_factory=OpenAIUsage)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SearchRequest(BaseModel):
    parcel_number: str = Field(min_length=3, max_length=40)
    captcha_token: str | None = Field(default=None, max_length=2048)
    analytics_consent: bool = False
    consent_version: Literal["1.2"] | None = None
    force_refresh: bool = False


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    captcha_token: str | None = Field(default=None, max_length=2048)


class PrivacyEventRequest(BaseModel):
    event_type: Literal["parcel_search"]
    parcel_reference: str = Field(min_length=3, max_length=40)
    analytics_consent: Literal[True]
    consent_version: Literal["1.2"]


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobView(BaseModel):
    id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    message: str
    parcel_number: str
    created_at: datetime
    updated_at: datetime
    result: SearchResult | None = None
    error: str | None = None
    from_cache: bool = False
    cache_stored_at: datetime | None = None
    cache_expires_at: datetime | None = None
