from pathlib import Path


def write_text_report(lines, path="_dl/MAX_REPORT.txt"):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return str(target)
