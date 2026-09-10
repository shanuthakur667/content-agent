from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ContentType = Literal["video", "reel"]
ContentStatus = Literal["draft", "final", "used", "published"]
ReelSlot = Literal["topic-teaser", "topic-clip", "topic-followup", "news-reactive", "evergreen"]


class ScoutFinding(BaseModel):
    claim: str = Field(min_length=10)
    url: str
    published: str
    source_type: str
    excerpt: str
    confidence: Literal["high", "medium", "low"]
    why_it_matters: str

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("published")
    @classmethod
    def published_is_date_or_unknown(cls, v: str) -> str:
        if v == "unknown":
            return v
        datetime.strptime(v, "%Y-%m-%d")
        return v


class CalendarEntry(BaseModel):
    week: str
    type: ContentType
    topic: str
    file: str
    status: ContentStatus = "draft"
    slot: ReelSlot | None = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    published_at: str | None = None


class FactCheckVerdict(BaseModel):
    draft: str
    verdict: Literal["PASS", "FAIL"]
    report_path: str
    required_fixes: list[str] = []
