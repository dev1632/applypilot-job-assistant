"""Greenhouse public Job Board API: https://developers.greenhouse.io/job-board.html"""
from __future__ import annotations

from datetime import datetime

from backend.sources.base import RawPosting, SourceAdapter, html_to_text


class GreenhouseAdapter(SourceAdapter):
    name = "greenhouse"

    def fetch(self) -> list[RawPosting]:
        data = self._get_json(f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs", content="true")
        out = []
        for j in data.get("jobs", []):
            first_published = j.get("first_published")
            posted = first_published or None
            out.append(RawPosting(
                source=self.name,
                source_job_id=str(j["id"]),
                company=j.get("company_name") or self.company,
                title=j.get("title", "").strip(),
                location=(j.get("location") or {}).get("name"),
                employment_type=None,
                url=j["absolute_url"],
                requisition_id=j.get("requisition_id"),
                description=html_to_text(j.get("content")),
                posted_at=datetime.fromisoformat(posted) if posted else None,
                # updated_at is not a publication date, so without first_published we don't claim one.
                freshness_confidence="VERIFIED" if first_published else "UNVERIFIED",
            ))
        return out
