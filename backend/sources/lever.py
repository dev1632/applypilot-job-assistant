"""Lever public Postings API: https://github.com/lever/postings-api"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.sources.base import RawPosting, SourceAdapter, html_to_text

_COMMITMENT = {"full time": "full-time", "full-time": "full-time", "intern": "internship",
               "internship": "internship", "contract": "contract", "part time": "part-time",
               "part-time": "part-time"}


class LeverAdapter(SourceAdapter):
    name = "lever"

    def fetch(self) -> list[RawPosting]:
        data = self._get_json(f"https://api.lever.co/v0/postings/{self.board}", mode="json")
        out = []
        for p in data:
            cats = p.get("categories") or {}
            sections = "\n\n".join(f"{s.get('text', '')}\n{html_to_text(s.get('content'))}" for s in p.get("lists", []))
            description = "\n\n".join(x for x in (p.get("descriptionPlain"), sections, p.get("additionalPlain")) if x)
            created = p.get("createdAt")
            location = cats.get("location")
            if p.get("workplaceType") == "remote" and location and "remote" not in location.lower():
                location = f"{location} (Remote)"
            commitment = (cats.get("commitment") or "").strip().lower()
            out.append(RawPosting(
                source=self.name,
                source_job_id=p["id"],
                company=self.company,
                title=p.get("text", "").strip(),
                location=location,
                employment_type=_COMMITMENT.get(commitment) or (commitment or None),
                url=p["hostedUrl"],
                requisition_id=None,
                description=description,
                posted_at=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None,
                freshness_confidence="ESTIMATED" if created else "UNVERIFIED",
            ))
        return out
