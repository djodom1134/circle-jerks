from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field


PRESETS = {
    "just_the_facts": {"anger": 0, "niceness": 5, "respect": 5, "detail": 10, "local": 0},
    "concerned_neighbor": {"anger": 3, "niceness": 6, "respect": 7, "detail": 6, "local": 3},
    "fed_up_resident": {"anger": 7, "niceness": 2, "respect": 4, "detail": 7, "local": 6},
    "formal_complaint": {"anger": 1, "niceness": 3, "respect": 10, "detail": 9, "local": 1},
    "community_voice": {"anger": 4, "niceness": 7, "respect": 6, "detail": 5, "local": 8},
}


class ToneSliders(BaseModel):
    anger: int = Field(default=3, ge=0, le=10)
    niceness: int = Field(default=6, ge=0, le=10)
    respect: int = Field(default=7, ge=0, le=10)
    detail: int = Field(default=6, ge=0, le=10)
    local: int = Field(default=3, ge=0, le=10)

    def stable_hash(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Band:
    min_value: int
    max_value: int
    text: str


BANDS = {
    "anger": [
        Band(0, 1, "Use neutral, factual language. No emotional content."),
        Band(2, 4, "Maintain a measured tone; minor expressions of concern are acceptable."),
        Band(5, 6, "Express clear frustration; the reader should understand this affects the resident's quality of life."),
        Band(7, 8, "Convey strong frustration and urgency; use emphatic phrasing but remain civil."),
        Band(9, 10, "Convey serious anger and exhaustion with the ongoing situation. No profanity, no personal attacks."),
    ],
    "niceness": [
        Band(0, 1, "Transactional and cold. No pleasantries."),
        Band(2, 4, "Neutral; skip pleasantries but remain civil."),
        Band(5, 6, "Acknowledge the reader as a person doing their job."),
        Band(7, 8, "Warm and cordial; acknowledge the shared goal of a livable community."),
        Band(9, 10, "Very warm; express appreciation for the reader's work and willingness to engage."),
    ],
    "respect": [
        Band(0, 1, "Direct and blunt; no honorifics, no thanks."),
        Band(2, 4, "Direct; skip formalities."),
        Band(5, 6, "Professional and appropriately formal."),
        Band(7, 8, "Deferential; thank the reader for their time."),
        Band(9, 10, "Highly deferential; formal salutation, explicit thanks, acknowledge authority's role."),
    ],
    "detail": [
        Band(0, 2, "3 sentences. Include only the highest-impact numbers: total events and minimum altitude."),
        Band(3, 5, "4 to 5 sentences. Include event counts by type and peak hours."),
        Band(6, 7, "5 to 6 sentences. Include all counts, altitudes, loop radius, and origin airport."),
        Band(8, 10, "7 to 8 sentences. Include every metric in the data, specific timestamps, and runway used if known."),
    ],
    "local": [
        Band(0, 1, "No place references beyond the airport name."),
        Band(2, 4, "Mention the user's city once."),
        Band(5, 6, "Mention the user's city and the neighborhood context, such as a residential area or quiet street."),
        Band(7, 8, "Frame the message as a resident of the specific community; mention local geography if relevant."),
        Band(9, 10, "Write from a strong local-resident perspective; reference nearby landmarks or neighborhood character naturally, without dialect or slang."),
    ],
}


def sliders_from_request(
    preset: str | None,
    anger: int,
    niceness: int,
    respect: int,
    detail: int,
    local: int,
) -> ToneSliders:
    if preset:
        key = preset.lower().replace(" ", "_").replace("-", "_")
        if key in PRESETS:
            return ToneSliders(**PRESETS[key])
    return ToneSliders(
        anger=anger,
        niceness=niceness,
        respect=respect,
        detail=detail,
        local=local,
    )


def band_for(name: str, value: int) -> str:
    for band in BANDS[name]:
        if band.min_value <= value <= band.max_value:
            return band.text
    raise ValueError(f"no tone band configured for {name}={value}")


def prompt_bands(sliders: ToneSliders) -> dict[str, str]:
    values = sliders.model_dump()
    return {name: band_for(name, value) for name, value in values.items()}

