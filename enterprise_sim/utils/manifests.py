"""Utilities for loading YAML manifests from disk with templating."""

from pathlib import Path
from string import Template
from typing import Any, Dict, List, Union

import yaml


def _resolve_path(path: Union[str, Path]) -> Path:
    """Resolve a manifest path relative to the project root."""

    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = Path(__file__).resolve().parents[2] / resolved
    return resolved


def render_manifest(path: Union[str, Path], **values: Any) -> str:
    """Load a manifest file and substitute template variables.

    Placeholders use ``string.Template`` syntax (e.g. ``$namespace``).
    Additional keyword arguments are optional.
    """

    path = _resolve_path(path)
    template = Template(path.read_text(encoding='utf-8'))
    return template.safe_substitute(**values)


def load_manifest_documents(path: Union[str, Path], **values: Any) -> List[Dict[str, Any]]:
    """Load one or more YAML documents from a manifest file.

    Returns a list of dictionaries. Single-document files return a list with
    one item so callers can uniformly iterate.
    """

    rendered = render_manifest(path, **values)
    return [doc for doc in yaml.safe_load_all(rendered) if doc is not None]


def load_single_manifest(path: Union[str, Path], **values: Any) -> Dict[str, Any]:
    """Load a single-document manifest and return it as a dictionary."""

    documents = load_manifest_documents(path, **values)
    if not documents:
        raise ValueError(f"Manifest '{path}' did not contain any documents")
    if len(documents) != 1:
        raise ValueError(f"Manifest '{path}' contains multiple documents")
    return documents[0]
