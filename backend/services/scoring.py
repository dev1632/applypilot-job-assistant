"""Transparent role-fit scoring: every score comes with reasons and gaps."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.models.profile import ROLE_FAMILIES, Profile
from backend.services.llm import JobAnalysis

# Common skills used to find gaps when no LLM analysis is available.
SKILL_VOCAB = [
    "Python", "Java", "Go", "C++", "C#", "JavaScript", "TypeScript", "Rust", "Scala", "Kotlin", "SQL",
    "React", "Node.js", "Next.js", "Angular", "Vue", "Django", "Flask", "FastAPI", "Spring",
    "AWS", "GCP", "Azure", "Docker", "Kubernetes", "Terraform", "Linux", "Git", "CI/CD",
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Kafka", "Spark", "Airflow", "dbt", "Snowflake",
    "PyTorch", "TensorFlow", "scikit-learn", "LLM", "NLP", "Computer Vision", "MLOps",
    "GraphQL", "REST", "Microservices", "Distributed Systems",
]
ALIASES = {"golang": "go", "k8s": "kubernetes", "postgres": "postgresql", "node": "node.js",
           "nodejs": "node.js", "js": "javascript", "ts": "typescript", "sklearn": "scikit-learn",
           "large language models": "llm", "llms": "llm", "amazon web services": "aws", "google cloud": "gcp",
           "google cloud platform": "gcp", "react.js": "react", "reactjs": "react", "vue.js": "vue",
           "vuejs": "vue", "angularjs": "angular", "angular.js": "angular", "next": "next.js", "nextjs": "next.js",
           "express.js": "express", "expressjs": "express", "spring": "spring boot", "springboot": "spring boot",
           "ci/cd pipelines": "ci/cd", "cicd": "ci/cd", "continuous integration": "ci/cd", "restful": "rest",
           "rest apis": "rest", "restful apis": "rest", "pyspark": "apache spark", "spark": "apache spark",
           "apache kafka": "kafka", "apache airflow": "airflow", "postgresql database": "postgresql",
           "c sharp": "c#", "cpp": "c++", "ml": "machine learning", "gen ai": "generative ai", "genai": "generative ai"}


def canon(skill: str) -> str:
    s = skill.strip().lower()
    return ALIASES.get(s, s)


def _mentions(skill: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9+#])", text) is not None


@dataclass
class FitScore:
    score: float
    role_family: str | None
    reasons: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


def role_family_for(title: str, families: list[str]) -> str | None:
    # Whole words only: "llm" must not match "fulfillment", nor "etl" or "sde" inside other words.
    t = title.lower()
    for fam in families:
        if any(re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t) for k in ROLE_FAMILIES.get(fam, [])):
            return fam
    return None


def score_fit(profile: Profile, *, title: str, description: str,
              analysis: JobAnalysis | None = None) -> FitScore:
    text = (description or "").lower()
    mine = {canon(s) for s in profile.skills}
    for exp in profile.experience:
        mine |= {canon(s) for s in exp.technologies}
    for proj in profile.projects:
        mine |= {canon(s) for s in proj.technologies}

    family = role_family_for(title, profile.preferences.role_families)
    reasons: list[str] = []
    if family:
        reasons.append(f"Title matches role family '{family}'")

    if analysis:
        required = [canon(s) for s in analysis.required_skills]
        preferred = [canon(s) for s in analysis.preferred_skills]
    else:
        required = [canon(s) for s in SKILL_VOCAB if _mentions(canon(s), text)]
        preferred = []

    req_hit = [s for s in required if s in mine]
    pref_hit = [s for s in preferred if s in mine]
    if req_hit:
        reasons.append(f"Matches {len(req_hit)}/{len(required)} required skills: {', '.join(req_hit)}")
    if pref_hit:
        reasons.append(f"Matches preferred skills: {', '.join(pref_hit)}")
    gaps = [s for s in required if s not in mine]
    if analysis and analysis.min_years_experience:
        gaps.append(f"Asks for {analysis.min_years_experience}+ years of experience")

    req_ratio = len(req_hit) / len(required) if required else 0.5
    pref_ratio = len(pref_hit) / len(preferred) if preferred else 0.0
    score = 0.7 * req_ratio + 0.1 * pref_ratio + (0.2 if family else 0.0)
    return FitScore(score=round(min(score, 1.0), 3), role_family=family, reasons=reasons, gaps=gaps)
