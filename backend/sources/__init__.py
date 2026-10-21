from backend.models.profile import SourceConfig
from backend.sources.base import RawPosting, SourceAdapter
from backend.sources.greenhouse import GreenhouseAdapter
from backend.sources.lever import LeverAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter}


def build_adapter(cfg: SourceConfig) -> SourceAdapter:
    try:
        return ADAPTERS[cfg.type](cfg.board, cfg.company)
    except KeyError:
        raise ValueError(f"Unknown source type '{cfg.type}'. Available: {sorted(ADAPTERS)}") from None


__all__ = ["ADAPTERS", "RawPosting", "SourceAdapter", "build_adapter"]
