"""Output hooks that summarize ingestion results for trace blocks."""

from collections import Counter
from typing import Any

from app.core import tracing


def chunks_output(chunks: Any) -> Any:
    if not isinstance(chunks, list):
        return chunks
    by_type = Counter(str(c.get("chunk_type")) for c in chunks if isinstance(c, dict))
    tracing.set_attributes(**{"chunks.count": len(chunks), "chunks.by_type": tracing.to_text(dict(by_type))})
    return {"count": len(chunks), "by_type": dict(by_type), "first": [
        {k: c.get(k) for k in ("sequence_id", "chunk_type", "page_start", "plain_text")} for c in chunks[:5] if isinstance(c, dict)
    ]}


def extractor_output(result: Any) -> Any:
    if isinstance(result, tuple) and len(result) == 2:
        tracing.set_attributes(extractor=str(result[1]))
        return {"output_dir": str(result[0]), "extractor": result[1]}
    return result


def images_output(images: Any) -> Any:
    if isinstance(images, list):
        tracing.set_attributes(**{"assets.found": len(images)})
        return {"found": len(images), "names": [getattr(p, "name", str(p)) for p in images[:20]]}
    return images


def article_output(result: Any) -> Any:
    import dataclasses
    if dataclasses.is_dataclass(result):
        data = dataclasses.asdict(result)
        asset_map = data.get("asset_map")
        is_map = isinstance(asset_map, dict)
        image_names = list(asset_map.keys())[:20] if is_map else []
        tracing.set_attributes(**{
            "article.images": len(asset_map) if is_map else 0,
            "article.image_names": tracing.to_text(image_names),
        })
        summary = {k: (v if not isinstance(v, str) or len(v) < 2000 else f"<{len(v)} chars>") for k, v in data.items()}
        if is_map:
            summary["asset_map"] = {"count": len(asset_map)}
        return summary
    return result
