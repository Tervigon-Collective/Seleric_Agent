"""Loads the knowledge corpus off disk.

The corpus is a directory of markdown files. Each file is one document; its
first ``# heading`` (or failing that, its filename) is the title, and an
optional ``type:`` line in a leading front-matter block classifies it as an
incident / SOP / model card / experiment note.

**The corpus ships empty.** Nothing in this system writes operator documents,
and inventing them would be fabricating institutional knowledge. An empty
corpus is a *miss*, not an error — ``CONTRACTS.md`` §2 explicitly permits the
Knowledge toolset to return ``success=True`` with zero artifacts, and
``search_knowledge`` says plainly that the corpus is empty rather than
implying the query simply found nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from seleric_swarm.paths import repo_root
from seleric_swarm.toolsets import policy_config as policy

#: Document classes the contract names. Anything else is kept but labelled
#: "note" rather than silently coerced into one of these.
KNOWN_TYPES = ("incident", "sop", "model_card", "experiment_note", "note")

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TYPE_LINE = re.compile(r"^type:\s*(\S+)\s*$", re.MULTILINE)
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    doc_type: str
    text: str
    path: str

    def citation(self) -> str:
        """How this document is referred to in a ToolResult. Path included so a
        reader can go check the source — a citation you cannot follow is not a
        citation."""
        return f"{self.title} ({self.doc_type}, {self.path})"


def corpus_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / policy.KNOWLEDGE_CORPUS_DIRNAME


def load_corpus(directory: Path | None = None) -> list[Document]:
    """Read every ``*.md`` under the corpus directory.

    A missing directory returns an empty list rather than raising: "no corpus
    configured" and "corpus configured but empty" are the same answer to the
    caller, and neither is an exceptional condition.

    Unreadable files are skipped rather than failing the whole search — one
    bad file should not make the corpus unavailable.
    """
    directory = directory or corpus_dir()
    if not directory.is_dir():
        return []

    out: list[Document] = []
    for path in sorted(directory.rglob("*.md")):
        if path.name.upper().startswith("README"):
            continue  # the ingestion instructions are not a knowledge document
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.append(_parse(path, raw, directory))
    return out


def _parse(path: Path, raw: str, root: Path) -> Document:
    doc_type = "note"
    body = raw
    front = _FRONT_MATTER.match(raw)
    if front:
        body = raw[front.end() :]
        declared = _TYPE_LINE.search(front.group(1))
        if declared:
            candidate = declared.group(1).strip().lower()
            # Keep an unrecognized type visible as "note" rather than dropping
            # the document or trusting an arbitrary string downstream.
            doc_type = candidate if candidate in KNOWN_TYPES else "note"

    heading = _H1.search(body)
    title = heading.group(1) if heading else path.stem.replace("_", " ")

    return Document(
        doc_id=path.stem,
        title=title,
        doc_type=doc_type,
        text=body.strip(),
        path=str(path.relative_to(root)).replace("\\", "/"),
    )
