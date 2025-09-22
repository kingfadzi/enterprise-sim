"""MinIO service definition backed by service manifest metadata."""

from typing import Optional, Dict, Any

from .manifest_service import ManifestService
from .manifest_def import load_service_manifest
from ..core.config import ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


class MinioService(ManifestService):
    """Manifest-driven MinIO service implementation."""

    @property
    def name(self) -> str:  # type: ignore[override]
        """Return service name even before manifest initialization."""
        if getattr(self, 'definition', None):
            return super().name
        return 'minio'

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

    def setup_external_access(self, domain: str, gateway_name: str) -> bool:
        """Backwards-compatible hook for CLI expectations."""
        print("External access for MinIO is managed via manifests; no additional setup required.")
        return True

    def is_installed(self) -> bool:
        """Check if MinIO is installed by looking for the operator release and tenant."""
        try:
            # Check if minio-operator Helm release exists (search across all namespaces)
            releases = self.helm.list_releases()
            operator_installed = any(release['name'] == 'minio-operator' for release in releases)

            if not operator_installed:
                return False

            # Check if enterprise-sim tenant exists and is initialized
            try:
                tenant = self.k8s.custom_objects.get_namespaced_custom_object(
                    group='minio.min.io',
                    version='v2',
                    namespace='minio-system',
                    plural='tenants',
                    name='enterprise-sim'
                )
                return tenant.get('status', {}).get('currentState') == 'Initialized'
            except:
                return False

        except Exception:
            return False
