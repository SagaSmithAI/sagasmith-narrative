"""Bounded read-only access to installed narrative Skills."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

MAX_RESULT_CHARS = 12_000


class SkillCatalog:
    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.environ.get("SAGASMITH_NARRATIVE_SKILLS_DIR")
        default_root = Path(__file__).resolve().parents[4] / "skills"
        self.root = Path(configured).resolve() if configured else default_root

    def _files(self) -> list[Path]:
        if self.root is None or not self.root.is_dir():
            return []
        return sorted(path for path in self.root.rglob("SKILL.md") if path.is_file())

    def list(self) -> list[dict[str, str]]:
        return [
            {"id": path.parent.name, "path": path.relative_to(self.root).as_posix()}
            for path in self._files()
        ]

    def _path(self, skill_id: str) -> Path:
        matches = [path for path in self._files() if path.parent.name == skill_id]
        if len(matches) != 1:
            raise LookupError(skill_id)
        return matches[0]

    def sections(self, skill_id: str) -> list[str]:
        text = self._path(skill_id).read_text(encoding="utf-8")
        return [
            match.group(1).strip() for match in re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
        ]

    def get_section(self, skill_id: str, section: str) -> dict[str, Any]:
        text = self._path(skill_id).read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^(#{{1,6}})\s+{re.escape(section)}\s*$", re.MULTILINE | re.IGNORECASE
        )
        match = pattern.search(text)
        if not match:
            raise LookupError(section)
        level = len(match.group(1))
        next_header = re.compile(rf"^#{{1,{level}}}\s+", re.MULTILINE).search(text, match.end())
        value = text[match.start() : next_header.start() if next_header else len(text)]
        if len(value) > MAX_RESULT_CHARS:
            value = value[:MAX_RESULT_CHARS]
            truncated = True
        else:
            truncated = False
        return {"skill_id": skill_id, "section": section, "content": value, "truncated": truncated}

    def search(self, query: str, *, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        terms = [item.casefold() for item in query.split() if item.strip()]
        if not terms:
            raise ValueError("search query is required")
        raw_cursor = str(cursor or "").strip()
        if raw_cursor and (not raw_cursor.startswith("p:") or not raw_cursor[2:].isdigit()):
            raise ValueError("cursor is invalid; reuse next_cursor from the preceding response")
        offset = int(raw_cursor[2:]) if raw_cursor else 0
        hits = []
        total_chars = 0
        matched = 0
        for path in self._files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                folded = line.casefold()
                if not all(term in folded for term in terms):
                    continue
                if matched < offset:
                    matched += 1
                    continue
                excerpt = line[:500]
                if total_chars + len(excerpt) > MAX_RESULT_CHARS or len(hits) >= limit:
                    return {
                        "query": query,
                        "hits": hits,
                        "next_cursor": f"p:{offset + len(hits)}",
                    }
                hits.append({"skill_id": path.parent.name, "line": number, "excerpt": excerpt})
                total_chars += len(excerpt)
                matched += 1
        return {"query": query, "hits": hits, "next_cursor": None}
