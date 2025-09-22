"""MinIO service definition backed by service manifest metadata."""

from typing import Optional, Dict, Any

from .manifest_service import ManifestService
from .manifest_def import load_service_manifest
from ..core.config import ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


class MinioService(ManifestService):
    """Manifest-driven MinIO service implementation."""

    def __init__(
        self,
        config: ServiceConfig,
        k8s_client: KubernetesClient,
        helm_client: HelmClient,
        global_context: Optional[Dict[str, Any]] = None,
    ):
        definition = load_service_manifest('minio')
        super().__init__(definition, config, k8s_client, helm_client, global_context)

    def get_endpoints(self, domain: str):  # type: ignore[override]
        # Defer to manifest-configured endpoints.
        return super().get_endpoints(domain)
