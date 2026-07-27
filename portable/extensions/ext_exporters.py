from pathlib import Path


def _ensure_parent(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def to_parquet(df, path):
    target = _ensure_parent(path)
    try:
        df.to_parquet(target, index=True)
    except Exception as exc:
        raise RuntimeError("Parquet export failed. This helper is optional and needs pandas + parquet backend.") from exc
    return str(target)


def to_feather(df, path):
    target = _ensure_parent(path)
    try:
        df.reset_index().to_feather(target)
    except Exception as exc:
        raise RuntimeError("Feather export failed. This helper is optional and needs pandas + feather backend.") from exc
    return str(target)


def to_csv(df, path):
    target = _ensure_parent(path)
    df.to_csv(target, index=True)
    return str(target)


def to_jsonl(records, path):
    import json

    target = _ensure_parent(path)
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return str(target)
