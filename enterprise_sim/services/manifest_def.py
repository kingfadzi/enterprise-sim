"""Service manifest definitions for plugin-style services."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

MANIFEST_ROOT = Path("manifests/services")


@dataclass
class WaitForSpec:
    type: str
    name: Optional[str] = None
    namespace: Optional[str] = None
    timeout: Optional[int] = None
    group: Optional[str] = None
    version: Optional[str] = None
    plural: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None


@dataclass
class InstallStep:
    step_type: str
    path: Optional[str] = None
    namespace: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    release: Optional[str] = None
    chart: Optional[str] = None
    repo: Optional[Dict[str, str]] = None
    values: Optional[Dict[str, Any]] = None
    wait_for: List[WaitForSpec] = field(default_factory=list)


@dataclass
class ValidationSpec:
    type: str
    name: Optional[str] = None
    namespace: Optional[str] = None
    group: Optional[str] = None
    version: Optional[str] = None
    plural: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None


@dataclass
class EndpointTemplate:
    name: str
    url: str
    type: str


@dataclass
class ServiceManifest:
    service_id: str
    display_name: str
    namespace: Optional[str]
    description: Optional[str]
    version: Optional[str]
    dependencies: List[str] = field(default_factory=list)
    config_defaults: Dict[str, Any] = field(default_factory=dict)
    install: List[InstallStep] = field(default_factory=list)
    validations: List[ValidationSpec] = field(default_factory=list)
    endpoints: List[EndpointTemplate] = field(default_factory=list)


_manifest_cache: Dict[str, ServiceManifest] = {}


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open(encoding='utf-8') as fh:
        return yaml.safe_load(fh) or {}


def load_service_manifest(service_id: str) -> ServiceManifest:
    if service_id in _manifest_cache:
        return _manifest_cache[service_id]

    manifest_path = MANIFEST_ROOT / service_id / "service.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Service manifest not found: {manifest_path}")

    data = _load_yaml(manifest_path)
    manifest = _build_manifest(service_id, data)
    _manifest_cache[service_id] = manifest
    return manifest


def load_all_service_manifests() -> List[ServiceManifest]:
    manifests: List[ServiceManifest] = []
    if not MANIFEST_ROOT.exists():
        return manifests

    for service_dir in sorted(MANIFEST_ROOT.iterdir()):
        if not service_dir.is_dir():
            continue
        manifest_path = service_dir / "service.yaml"
        if not manifest_path.exists():
            continue
        service_id = service_dir.name
        manifests.append(load_service_manifest(service_id))
    return manifests


def _build_manifest(service_id: str, data: Dict[str, Any]) -> ServiceManifest:
    install_steps = [
        InstallStep(
            step_type=step.get('type', 'manifest'),
            path=step.get('path'),
            namespace=step.get('namespace'),
            context=step.get('context', {}),
            release=step.get('release'),
            chart=step.get('chart'),
            repo=step.get('repo'),
            values=step.get('values'),
            wait_for=[WaitForSpec(**wait) for wait in step.get('wait_for', [])],
        )
        for step in data.get('install', [])
    ]

    validations = [ValidationSpec(**spec) for spec in data.get('validations', [])]
    endpoints = [EndpointTemplate(**ep) for ep in data.get('endpoints', [])]

    return ServiceManifest(
        service_id=service_id,
        display_name=data.get('name', service_id.title()),
        namespace=data.get('namespace'),
        description=data.get('description'),
        version=data.get('version'),
        dependencies=data.get('dependencies', []),
        config_defaults=data.get('config_defaults', {}),
        install=install_steps,
        validations=validations,
        endpoints=endpoints,
    )
