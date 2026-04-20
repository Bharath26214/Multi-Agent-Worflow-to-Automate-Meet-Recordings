from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    sys.path.insert(0, str(src_root))

    from graph.workflow import build_graph  # noqa: WPS433

    app = build_graph()
    graph_view = app.get_graph()

    output_dir = repo_root / "demo"
    output_dir.mkdir(parents=True, exist_ok=True)

    mermaid_text = graph_view.draw_mermaid()
    mermaid_path = output_dir / "workflow_graph.mmd"
    mermaid_path.write_text(mermaid_text, encoding="utf-8")
    print(f"Mermaid graph written: {mermaid_path}")

    try:
        png_bytes = graph_view.draw_mermaid_png()
        png_path = output_dir / "workflow_graph.png"
        png_path.write_bytes(png_bytes)
        print(f"PNG graph written: {png_path}")
    except Exception as exc:
        print("PNG export unavailable, Mermaid file still created.")
        print(f"Reason: {exc}")


if __name__ == "__main__":
    main()

