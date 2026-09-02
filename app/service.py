from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .ai_summary import ParcelSummarizer
from .config import Settings
from .errors import CheckerError, DocumentDownloadError, PDFExtractionError
from .gurs import GURSClient, GURSParcel, parse_parcel_reference
from .models import (
    DocumentResult,
    PlanningActResult,
    PlanningMapEvidence,
    SearchResult,
)
from .pdf_downloader import PISArchiveDownloader
from .pdf_parser import PDFParser, find_parcel_mentions
from .pip_extractor import (
    PlanningTextSource,
    extract_planning_conditions,
    extract_preemption_right,
    is_textual_planning_document,
)
from .pis import PISClient
from .planning_map import (
    MapDocumentMatch,
    build_planning_land_use_map,
    inspect_map_document,
    render_pdf_map_preview,
)
from .site_analysis import SiteAnalysisClient, assess_land_use


ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class _MapCandidate:
    act_id: int
    act_title: str
    pdf_title: str
    pdf_download_url: str
    source_path: str
    path: Path
    checksum: str
    match: MapDocumentMatch


class ParcelSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def search(self, raw_reference: str, progress: ProgressCallback) -> SearchResult:
        reference = parse_parcel_reference(raw_reference)
        progress(5, "Looking up the parcel in GURS…")
        gurs = GURSClient(self.settings)
        pis = PISClient(self.settings)
        site_analysis = SiteAnalysisClient(self.settings)
        downloader = PISArchiveDownloader(self.settings)
        parser = PDFParser(self.settings)
        summarizer = ParcelSummarizer(self.settings)
        warnings: list[str] = []
        documents: list[DocumentResult] = []
        planning_text_sources: list[PlanningTextSource] = []
        map_candidates: list[_MapCandidate] = []
        try:
            parcel = gurs.get_parcel(reference)
            progress(18, "Checking PIS planning layers…")
            acts = pis.find_acts(parcel)
            contexts = pis.planning_context(parcel)
            land_use_assessment = assess_land_use(parcel, contexts)
            progress(24, "Checking utilities, access, protected areas, and risks…")
            site_result = site_analysis.analyze(parcel)
            warnings.extend(site_result.warnings)
            if not acts:
                warnings.append(
                    "PIS returned no planning-act polygons intersecting this parcel."
                )
            total_acts = max(1, len(acts))
            for act_index, act in enumerate(acts):
                base_progress = 31 + int((act_index / total_acts) * 62)
                progress(
                    base_progress,
                    f"Downloading PIS material {act_index + 1}/{len(acts)}: {act.title}",
                )
                try:
                    pdfs = downloader.download_pdfs(act)
                except DocumentDownloadError as exc:
                    warnings.append(str(exc))
                    continue
                if not pdfs:
                    warnings.append(
                        f"No PDF files were present in the PIS archive for ‘{act.title}’."
                    )
                    continue
                act_context = [
                    item for item in contexts if item.act_id == act.procedure_id
                ]
                for pdf_index, cached_pdf in enumerate(pdfs):
                    progress(
                        min(
                            94,
                            base_progress
                            + int(((pdf_index + 1) / len(pdfs)) * (62 / total_acts)),
                        ),
                        f"Analyzing {cached_pdf.source_name}",
                    )
                    path = (
                        self.settings.data_dir
                        / "pdfs"
                        / str(act.procedure_id)
                        / cached_pdf.local_name
                    )
                    pdf_download_url = (
                        f"/api/files/{act.procedure_id}/{cached_pdf.local_name}"
                    )
                    extraction_warnings: list[str] = []
                    try:
                        pdf_text = parser.extract(path, cached_pdf.sha256)
                        extraction_warnings.extend(pdf_text.warnings)
                        mentions = find_parcel_mentions(
                            pdf_text.pages, reference.parcel_number
                        )
                        if (
                            act.preparation_state == "completed"
                            and is_textual_planning_document(cached_pdf.source_name)
                        ):
                            planning_text_sources.append(
                                PlanningTextSource(
                                    title=(
                                        f"{act.title} – "
                                        f"{Path(cached_pdf.source_name).name}"
                                    ),
                                    url=pdf_download_url,
                                    pages=pdf_text.pages,
                                )
                            )
                    except PDFExtractionError as exc:
                        extraction_warnings.append(str(exc))
                        mentions = find_parcel_mentions([], reference.parcel_number)
                    summary, provider, findings, summary_warnings = (
                        summarizer.summarize(
                            parcel_number=reference.canonical,
                            act_title=act.title,
                            pdf_title=cached_pdf.source_name,
                            mention_count=mentions.count,
                            excerpts=mentions.excerpts,
                            planning_context=act_context,
                        )
                    )
                    extraction_warnings.extend(summary_warnings)
                    documents.append(
                        DocumentResult(
                            act_id=act.procedure_id,
                            act_title=act.title,
                            act_type=act.act_type,
                            act_status=act.status or act.preparation_state,
                            act_page_url=act.page_url,
                            pdf_title=Path(cached_pdf.source_name).name,
                            pdf_download_url=pdf_download_url,
                            source_path=cached_pdf.source_name,
                            size_bytes=cached_pdf.size_bytes,
                            sha256=cached_pdf.sha256,
                            mention_count=mentions.count,
                            excerpts=mentions.excerpts,
                            summary=summary,
                            summary_provider=provider,
                            important_findings=findings,
                            extraction_warnings=extraction_warnings,
                        )
                    )
                    map_matches = inspect_map_document(
                        path,
                        cached_pdf.source_name,
                        parcel,
                        [excerpt.page for excerpt in mentions.excerpts],
                    )
                    for match in map_matches:
                        map_candidates.append(
                            _MapCandidate(
                                act_id=act.procedure_id,
                                act_title=act.title,
                                pdf_title=Path(cached_pdf.source_name).name,
                                pdf_download_url=pdf_download_url,
                                source_path=cached_pdf.source_name,
                                path=path,
                                checksum=cached_pdf.sha256,
                                match=match,
                            )
                        )
            progress(97, "Checking municipal pre-emption provisions…")
            preemption_right = extract_preemption_right(
                planning_text_sources, contexts, parcel.information.municipality
            )
            progress(98, "Preparing the result…")
            planning_conditions = extract_planning_conditions(
                planning_text_sources, contexts
            )
            documents.sort(
                key=lambda item: (-item.mention_count, item.act_title, item.pdf_title)
            )
            planning_acts = []
            for act in acts:
                act_documents = [
                    document
                    for document in documents
                    if document.act_id == act.procedure_id
                ]
                planning_acts.append(
                    PlanningActResult(
                        procedure_id=act.procedure_id,
                        act_id=act.act_id,
                        title=act.title,
                        act_type=act.act_type,
                        status=act.status,
                        preparation_state=act.preparation_state,
                        page_url=act.page_url,
                        document_count=len(act_documents),
                        literal_mention_count=sum(
                            document.mention_count for document in act_documents
                        ),
                    )
                )
            map_evidence = self._prepare_map_evidence(
                parcel,
                reference.parcel_number,
                map_candidates,
                {context.act_id for context in contexts if context.act_id is not None},
            )
            return SearchResult(
                parcel=parcel.information,
                planning_context=contexts,
                land_use_assessment=land_use_assessment,
                planning_acts=planning_acts,
                infrastructure=site_result.infrastructure,
                road_access=site_result.road_access,
                protected_areas=site_result.protected_areas,
                constraints=site_result.constraints,
                risks=site_result.risks,
                cultural_heritage=site_result.cultural_heritage,
                parcel_map=site_result.parcel_map,
                planning_land_use_map=build_planning_land_use_map(parcel, map_evidence),
                planning_conditions=planning_conditions,
                preemption_right=preemption_right,
                documents=documents,
                warnings=warnings,
                openai_usage=summarizer.usage,
            )
        except CheckerError:
            raise
        finally:
            gurs.close()
            pis.close()
            site_analysis.close()
            downloader.close()

    def _prepare_map_evidence(
        self,
        parcel: GURSParcel,
        parcel_number: str,
        candidates: list[_MapCandidate],
        current_act_ids: set[int],
    ) -> list[PlanningMapEvidence]:
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                candidate.act_id not in current_act_ids,
                candidate.match.match_method != "geospatial",
                -candidate.act_id,
                candidate.pdf_title,
            ),
        )
        selected: list[_MapCandidate] = []
        seen: set[tuple[str, int]] = set()
        for candidate in ordered:
            key = (candidate.checksum, candidate.match.page)
            if key in seen:
                continue
            seen.add(key)
            selected.append(candidate)
            if len(selected) == 3:
                break

        evidence: list[PlanningMapEvidence] = []
        for candidate in selected:
            preview_name = render_pdf_map_preview(
                path=candidate.path,
                checksum=candidate.checksum,
                parcel=parcel,
                parcel_number=parcel_number,
                match=candidate.match,
                destination=(
                    self.settings.data_dir / "map_previews" / str(candidate.act_id)
                ),
            )
            evidence.append(
                PlanningMapEvidence(
                    act_id=candidate.act_id,
                    act_title=candidate.act_title,
                    pdf_title=candidate.pdf_title,
                    pdf_download_url=(
                        f"{candidate.pdf_download_url}#page={candidate.match.page}"
                    ),
                    preview_url=(
                        f"/api/map-previews/{candidate.act_id}/{preview_name}"
                        if preview_name
                        else None
                    ),
                    page=candidate.match.page,
                    match_method=candidate.match.match_method,
                    source_path=candidate.source_path,
                )
            )
        return evidence
