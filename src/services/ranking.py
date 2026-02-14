"""Ranking service with comprehensive AI tracing.

This service demonstrates full traceability for AI-powered idea ranking,
capturing every decision step with explanations.
"""

import json
import re
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

import structlog

from ..config import Settings, get_settings
from ..models.idea import Idea
from ..models.ranking import Ranking
from ..tracing import SpanType, TraceType, Tracer
from ..tracing.providers import TracedAnthropicClient

logger = structlog.get_logger(__name__)


@dataclass
class SWOTAnalysis:
    """SWOT analysis result."""

    strengths: list[str]
    weaknesses: list[str]
    opportunities: list[str]
    threats: list[str]


@dataclass
class DimensionScore:
    """Score for a single dimension."""

    dimension: str
    score: float  # 0-100
    weight: float
    weighted_score: float
    reasoning: str


class RankingService:
    """Service for ranking ideas using Claude with full traceability.

    This service demonstrates comprehensive AI tracing:
    - Each ranking operation creates a trace
    - SWOT generation creates a span with reasoning steps
    - Each dimension score creates a span with explanation
    - All token usage and costs are tracked

    Example:
        service = RankingService(tracer, anthropic_client, settings)
        ranking = await service.rank_idea(idea)
        # Now you can query traces to see exactly why Claude scored it this way
    """

    DIMENSIONS = [
        ("solo_buildable", "Solo Buildable", "Can a single developer build this?"),
        ("resource_intensity", "Resource Intensity", "How many resources are needed?"),
        ("moat_potential", "Moat Potential", "Can this build defensibility?"),
        ("market_timing", "Market Timing", "Is the market ready for this?"),
        ("profitability_path", "Profitability Path", "Is there a clear path to profit?"),
        ("personal_fit", "Personal Fit", "Does this fit the founder's skills?"),
    ]

    def __init__(
        self,
        tracer: Tracer,
        anthropic: TracedAnthropicClient,
        settings: Optional[Settings] = None,
    ):
        """Initialize the ranking service.

        Args:
            tracer: Tracer instance for traceability
            anthropic: Traced Anthropic client
            settings: Optional settings (defaults to get_settings())
        """
        self.tracer = tracer
        self.anthropic = anthropic
        self.settings = settings or get_settings()
        self.logger = logger.bind(service="ranking")

    async def rank_idea(self, idea: Idea) -> Ranking:
        """Rank an idea with full traceability.

        Creates a complete trace of the ranking process including:
        - SWOT analysis generation
        - 6-dimension scoring with reasoning
        - Weighted score calculation
        - Recommendation generation

        Args:
            idea: The idea to rank

        Returns:
            Ranking with SWOT, scores, and recommendation
        """
        correlation_id = uuid4()

        async with self.tracer.start_trace(
            TraceType.RANKING,
            correlation_id=correlation_id,
            idea_id=idea.id,
            tags=["ranking", idea.source_type.value],
            metadata={
                "idea_name": idea.name,
                "source": idea.source_type.value,
            },
        ):
            self.logger.info("ranking_started", idea_id=idea.id, idea_name=idea.name)

            # Step 1: Generate SWOT analysis
            swot = await self._generate_swot(idea)

            # Step 2: Score each dimension
            dimension_scores = await self._score_all_dimensions(idea, swot)

            # Step 3: Calculate weighted overall score
            overall_score = self._calculate_overall_score(dimension_scores)

            # Step 4: Generate recommendation
            recommendation, action_items = await self._generate_recommendation(
                idea, swot, dimension_scores, overall_score
            )

            # Build ranking object
            ranking = Ranking(
                idea_id=idea.id,
                # SWOT
                strengths=swot.strengths,
                weaknesses=swot.weaknesses,
                opportunities=swot.opportunities,
                threats=swot.threats,
                # Dimension scores
                solo_buildable_score=dimension_scores["solo_buildable"].score,
                resource_intensity_score=dimension_scores["resource_intensity"].score,
                moat_potential_score=dimension_scores["moat_potential"].score,
                market_timing_score=dimension_scores["market_timing"].score,
                profitability_path_score=dimension_scores["profitability_path"].score,
                personal_fit_score=dimension_scores["personal_fit"].score,
                # Overall
                overall_score=overall_score,
                recommendation=recommendation,
                action_items=action_items,
                # Metadata
                model_used=self.settings.default_llm_model,
            )

            self.logger.info(
                "ranking_completed",
                idea_id=idea.id,
                overall_score=overall_score,
            )

            return ranking

    async def _generate_swot(self, idea: Idea) -> SWOTAnalysis:
        """Generate SWOT analysis for an idea."""
        system_prompt = """You are an expert startup analyst. Generate a SWOT analysis for the given product/startup idea.

Be specific and actionable. Each point should be 1-2 sentences.

Respond in JSON format:
{
    "strengths": ["strength 1", "strength 2", ...],
    "weaknesses": ["weakness 1", "weakness 2", ...],
    "opportunities": ["opportunity 1", "opportunity 2", ...],
    "threats": ["threat 1", "threat 2", ...]
}

Provide 3-5 points for each category."""

        user_prompt = f"""Analyze this idea:

Name: {idea.name}
Description: {idea.description or 'No description provided'}
Tagline: {idea.tagline or 'N/A'}
Tags: {', '.join(idea.tags or [])}
Source: {idea.source_type.value}"""

        async with self.tracer.start_span(
            SpanType.LLM_CALL,
            "swot_analysis",
            provider="anthropic",
            model=self.settings.default_llm_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        ) as span:
            response = await self.anthropic.create_message(
                model=self.settings.default_llm_model,
                max_tokens=1500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                span_name="swot_generation",
            )

            # Parse response
            response_text = response.content[0].text if response.content else ""
            swot_data = self._parse_json_response(response_text)

            swot = SWOTAnalysis(
                strengths=swot_data.get("strengths", []),
                weaknesses=swot_data.get("weaknesses", []),
                opportunities=swot_data.get("opportunities", []),
                threats=swot_data.get("threats", []),
            )

            # Record reasoning steps for each SWOT category
            for category, items in [
                ("strengths", swot.strengths),
                ("weaknesses", swot.weaknesses),
                ("opportunities", swot.opportunities),
                ("threats", swot.threats),
            ]:
                await span.record_reasoning(
                    step_type="swot_extraction",
                    description=f"Identified {len(items)} {category}",
                    output_result={category: items},
                    explanation=f"Claude identified {len(items)} {category} for this idea based on market analysis.",
                )

            return swot

    async def _score_all_dimensions(
        self, idea: Idea, swot: SWOTAnalysis
    ) -> dict[str, DimensionScore]:
        """Score all dimensions for an idea."""
        scores = {}
        weights = {
            "solo_buildable": self.settings.weight_solo_buildable,
            "resource_intensity": self.settings.weight_resource_intensity,
            "moat_potential": self.settings.weight_moat_potential,
            "market_timing": self.settings.weight_market_timing,
            "profitability_path": self.settings.weight_profitability_path,
            "personal_fit": self.settings.weight_personal_fit,
        }

        for dim_key, dim_name, dim_description in self.DIMENSIONS:
            score = await self._score_dimension(
                idea, swot, dim_key, dim_name, dim_description, weights[dim_key]
            )
            scores[dim_key] = score

        return scores

    async def _score_dimension(
        self,
        idea: Idea,
        swot: SWOTAnalysis,
        dim_key: str,
        dim_name: str,
        dim_description: str,
        weight: float,
    ) -> DimensionScore:
        """Score a single dimension."""
        system_prompt = f"""You are evaluating startup ideas on the dimension: {dim_name}

Question: {dim_description}

Consider the SWOT analysis provided and score this dimension from 0-100.
- 0-20: Very poor
- 21-40: Poor
- 41-60: Average
- 61-80: Good
- 81-100: Excellent

Respond in JSON format:
{{
    "score": <number 0-100>,
    "reasoning": "<1-2 sentence explanation>"
}}"""

        user_prompt = f"""Idea: {idea.name}
Description: {idea.description or 'No description'}

SWOT Analysis:
- Strengths: {', '.join(swot.strengths)}
- Weaknesses: {', '.join(swot.weaknesses)}
- Opportunities: {', '.join(swot.opportunities)}
- Threats: {', '.join(swot.threats)}

Score this idea on: {dim_name}"""

        async with self.tracer.start_span(
            SpanType.LLM_CALL,
            f"score_{dim_key}",
            provider="anthropic",
            model=self.settings.default_llm_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        ) as span:
            response = await self.anthropic.create_message(
                model=self.settings.default_llm_model,
                max_tokens=500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                span_name=f"score_{dim_key}",
            )

            response_text = response.content[0].text if response.content else ""
            score_data = self._parse_json_response(response_text)

            raw_score = float(score_data.get("score", 50))
            reasoning = score_data.get("reasoning", "No reasoning provided")
            weighted_score = raw_score * weight

            # Record the scoring reasoning
            await span.record_reasoning(
                step_type="score_calculation",
                description=f"Scored {dim_name} as {raw_score}/100",
                dimension=dim_key,
                raw_score=raw_score,
                weight_applied=weight,
                weighted_score=weighted_score,
                confidence=0.8,  # Could be derived from response certainty
                explanation=reasoning,
            )

            return DimensionScore(
                dimension=dim_key,
                score=raw_score,
                weight=weight,
                weighted_score=weighted_score,
                reasoning=reasoning,
            )

    def _calculate_overall_score(self, scores: dict[str, DimensionScore]) -> float:
        """Calculate weighted overall score."""
        total_weighted = sum(s.weighted_score for s in scores.values())
        total_weight = sum(s.weight for s in scores.values())
        return total_weighted / total_weight if total_weight > 0 else 0.0

    async def _generate_recommendation(
        self,
        idea: Idea,
        swot: SWOTAnalysis,
        scores: dict[str, DimensionScore],
        overall_score: float,
    ) -> tuple[str, list[str]]:
        """Generate a recommendation based on analysis."""
        scores_summary = "\n".join(
            f"- {s.dimension}: {s.score}/100 ({s.reasoning})" for s in scores.values()
        )

        system_prompt = """You are a startup advisor. Based on the analysis provided, give a clear recommendation and action items.

Respond in JSON format:
{
    "recommendation": "<2-3 sentence recommendation>",
    "action_items": ["action 1", "action 2", "action 3"]
}"""

        user_prompt = f"""Idea: {idea.name}
Overall Score: {overall_score:.1f}/100

Dimension Scores:
{scores_summary}

SWOT Summary:
- Key Strength: {swot.strengths[0] if swot.strengths else 'None'}
- Key Weakness: {swot.weaknesses[0] if swot.weaknesses else 'None'}
- Key Opportunity: {swot.opportunities[0] if swot.opportunities else 'None'}
- Key Threat: {swot.threats[0] if swot.threats else 'None'}

Provide a recommendation and 3-5 action items."""

        async with self.tracer.start_span(
            SpanType.LLM_CALL,
            "generate_recommendation",
            provider="anthropic",
            model=self.settings.default_llm_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        ) as span:
            response = await self.anthropic.create_message(
                model=self.settings.default_llm_model,
                max_tokens=800,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                span_name="recommendation",
            )

            response_text = response.content[0].text if response.content else ""
            rec_data = self._parse_json_response(response_text)

            recommendation = rec_data.get("recommendation", "Unable to generate recommendation")
            action_items = rec_data.get("action_items", [])

            await span.record_reasoning(
                step_type="recommendation_generation",
                description="Generated final recommendation based on analysis",
                output_result={
                    "recommendation": recommendation,
                    "action_items": action_items,
                },
                explanation=f"Based on overall score of {overall_score:.1f}/100, "
                f"Claude recommends: {recommendation[:100]}...",
            )

            return recommendation, action_items

    def _parse_json_response(self, text: str) -> dict:
        """Parse JSON from LLM response, handling markdown code blocks."""
        # Remove markdown code blocks if present
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self.logger.warning("json_parse_failed", text=text[:200])
            return {}
