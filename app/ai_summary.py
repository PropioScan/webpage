from __future__ import annotations

from pydantic import BaseModel, Field

from .config import Settings
from .models import Excerpt, ImportantFinding, Importance, OpenAIUsage, PlanningContext
from .parcel_analyzer import extract_findings, extractive_summary


class AISummaryFinding(BaseModel):
    category: str
    detail: str
    importance: Importance
    pages: list[int] = Field(default_factory=list)


class AISummaryResponse(BaseModel):
    summary: str
    important_findings: list[AISummaryFinding] = Field(default_factory=list)


class ParcelSummarizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.usage = OpenAIUsage(
            configured=bool(settings.openai_api_key),
            model=settings.openai_model if settings.openai_api_key else None,
        )

    def summarize(
        self,
        *,
        parcel_number: str,
        act_title: str,
        pdf_title: str,
        mention_count: int,
        excerpts: list[Excerpt],
        planning_context: list[PlanningContext],
    ) -> tuple[str, str, list[ImportantFinding], list[str]]:
        fallback_findings = extract_findings(excerpts)
        fallback = extractive_summary(parcel_number, excerpts, mention_count)
        if mention_count == 0 or not excerpts:
            return fallback, "prostorski presek PIS", fallback_findings, []
        if not self.settings.openai_api_key:
            return (
                fallback,
                "samodejni izvleček",
                fallback_findings,
                [],
            )

        warnings: list[str] = []
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.settings.openai_api_key)
            raw_response = client.responses.with_raw_response.parse(
                model=self.settings.openai_model,
                instructions=(
                    "You are a careful Slovenian spatial-planning document analyst. "
                    "Use only the supplied parcel-specific excerpts and structured PIS context. "
                    "Describe only facts that apply to the requested parcel. Do not infer a permission, "
                    "restriction, protected status, or owner obligation unless supported by the evidence. "
                    "Distinguish explicit PDF statements from spatial metadata. Cite PDF page numbers in each finding. "
                    f"Write in {self.settings.summary_language}. This is an informational summary, not legal advice."
                ),
                input=self._prompt(
                    parcel_number,
                    act_title,
                    pdf_title,
                    mention_count,
                    excerpts,
                    planning_context,
                ),
                text_format=AISummaryResponse,
            )
            response = raw_response.parse()
            self._record_usage(response, raw_response.http_response.headers)
            parsed = response.output_parsed
            if parsed is None:
                raise ValueError("The model did not return a parsed summary.")
            findings = [
                ImportantFinding(
                    category=item.category,
                    detail=item.detail,
                    importance=item.importance,
                    pages=sorted(set(item.pages)),
                )
                for item in parsed.important_findings
            ]
            return (
                parsed.summary,
                f"OpenAI {self.settings.openai_model}",
                findings,
                warnings,
            )
        except Exception as exc:
            warnings.append(
                f"Povzetka z umetno inteligenco ni bilo mogoče pripraviti ({type(exc).__name__}); prikazan je samodejni izvleček."
            )
            return (
                fallback,
                "samodejni izvleček",
                fallback_findings,
                warnings,
            )

    def _record_usage(self, response: object, headers: object) -> None:
        response_usage = getattr(response, "usage", None)
        if response_usage is not None:
            self.usage.calls += 1
            self.usage.input_tokens += int(
                getattr(response_usage, "input_tokens", 0) or 0
            )
            self.usage.output_tokens += int(
                getattr(response_usage, "output_tokens", 0) or 0
            )
            self.usage.total_tokens += int(
                getattr(response_usage, "total_tokens", 0) or 0
            )

        get_header = getattr(headers, "get", None)
        if get_header is None:
            return
        self.usage.rate_limit_tokens = self._optional_int(
            get_header("x-ratelimit-limit-tokens")
            or get_header("x-ratelimit-limit-project-tokens")
        )
        self.usage.rate_limit_remaining_tokens = self._optional_int(
            get_header("x-ratelimit-remaining-tokens")
            or get_header("x-ratelimit-remaining-project-tokens")
        )
        self.usage.rate_limit_reset = (
            get_header("x-ratelimit-reset-tokens")
            or get_header("x-ratelimit-reset-project-tokens")
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        try:
            return int(str(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _prompt(
        self,
        parcel_number: str,
        act_title: str,
        pdf_title: str,
        mention_count: int,
        excerpts: list[Excerpt],
        planning_context: list[PlanningContext],
    ) -> str:
        context_lines = [
            (
                f"- Act: {item.act_title}; land use: {item.land_use_code} "
                f"({item.land_use_description}); planning unit: {item.planning_unit}; subunit: {item.subunit}"
            )
            for item in planning_context
        ]
        excerpt_blocks: list[str] = []
        used = 0
        for excerpt in excerpts:
            block = f"[PDF page {excerpt.page}; section: {excerpt.section or 'unknown'}]\n{excerpt.text}\n"
            if used + len(block) > self.settings.max_summary_context_chars:
                break
            excerpt_blocks.append(block)
            used += len(block)
        return (
            f"Requested parcel: {parcel_number}\nPlanning act: {act_title}\nPDF: {pdf_title}\n"
            f"Literal mention count: {mention_count}\n\nStructured PIS spatial context:\n"
            + (
                "\n".join(context_lines)
                if context_lines
                else "- No structured land-use row returned."
            )
            + "\n\nParcel-specific PDF excerpts:\n"
            + "\n".join(excerpt_blocks)
            + "\nProduce one consolidated parcel-specific summary and a deduplicated list of important findings."
        )
