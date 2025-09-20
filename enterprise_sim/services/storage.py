"""OpenEBS storage service implementation."""

import time
from typing import Dict, Any, Set, List, Optional
from ..services.base import BaseService, ServiceStatus, ServiceHealth, ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


class OpenEBSService(BaseService):
    """OpenEBS LocalPV storage provisioner service."""

    def __init__(self, config: ServiceConfig, k8s_client: KubernetesClient, helm_client: HelmClient):
        super().__init__(config, k8s_client, helm_client)

    @property
    def name(self) -> str:
        """Service name."""
        return "storage"

    @property
    def namespace(self) -> str:
        """Default namespace for the service."""
        return "openebs-system"

    @property
    def dependencies(self) -> Set[str]:
        """Set of service names this service depends on."""
        return set()  # Storage has no dependencies

    @property
    def helm_chart(self) -> Optional[Dict[str, str]]:
        """Helm chart information."""
        return {
            'repo': 'openebs',
            'repo_url': 'https://openebs.github.io/charts',
            'chart': 'openebs'
        }

    def get_helm_values(self) -> Dict:
        """Get Helm values for OpenEBS installation."""
        return {
            # Disable engines we don't need
            "engines": {
                "local": {
                    "lvm": {"enabled": False},
                    "zfs": {"enabled": False},
                    "hostpath": {"enabled": True}
                },
                "replicated": {
                    "mayastor": {"enabled": False}
                }
            },
            # Configure LocalPV provisioner
            "localpv-provisioner": {
                "hostpathClass": {
                    "enabled": True,
                    "name": "openebs-hostpath",
                    "isDefaultClass": False
                }
            },
            # Disable node disk manager (not needed for hostpath)
            "ndm": {"enabled": False},
            "ndmOperator": {"enabled": False}
        }

    def validate_prerequisites(self) -> bool:
        """Validate prerequisites before installation."""
        # Check if cluster is accessible
        try:
            nodes = self.k8s.get_resource("nodes")
            if not nodes or not nodes.get("items"):
                print("ERROR: No cluster nodes found")
                return False
            print(f"Found {len(nodes.get('items', []))} cluster nodes")
            return True
        except Exception as e:
            print(f"ERROR: Cannot access cluster: {e}")
            return False

    def post_install_tasks(self) -> bool:
        """Execute post-installation tasks."""
        try:
            # Wait for OpenEBS components to be ready
            print("Waiting for OpenEBS components to be ready...")
            if not self._wait_for_openebs_ready():
                print("ERROR: OpenEBS components not ready within timeout")
                return False

            # Create enterprise storage classes
            print("Creating enterprise storage classes...")
            if not self._create_storage_classes():
                print("ERROR: Failed to create enterprise storage classes")
                return False

            return True
        except Exception as e:
            print(f"ERROR: Exception in post_install_tasks: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_endpoints(self) -> List[Dict[str, str]]:
        """Get service endpoints for external access."""
        # Storage service doesn't expose external endpoints
        return []


    def uninstall(self) -> bool:
        """Uninstall OpenEBS storage platform."""
        print("Uninstalling OpenEBS storage platform...")

        # Remove storage classes first
        self._remove_storage_classes()

        # Uninstall OpenEBS Helm chart
        if not self.helm.uninstall(self.name, self.namespace):
            print("ERROR: Failed to uninstall OpenEBS Helm chart")
            return False

        print("OpenEBS storage platform uninstalled")
        return True

    def get_health(self) -> ServiceHealth:
        """Get OpenEBS service health."""
        # Check if OpenEBS deployment exists
        deployment_name = f"{self.name}-openebs-localpv-provisioner"
        deployment = self.k8s.get_resource("deployment", deployment_name, self.namespace)
        if not deployment:
            return ServiceHealth.UNHEALTHY

        # Check deployment status
        status = deployment.get("status", {})
        ready_replicas = status.get("readyReplicas", 0)
        replicas = status.get("replicas", 0)

        if ready_replicas == replicas and replicas > 0:
            # Additional health checks - validate storage classes
            if self._validate_storage_classes():
                return ServiceHealth.HEALTHY
            else:
                return ServiceHealth.DEGRADED
        else:
            return ServiceHealth.DEGRADED


    def _wait_for_openebs_ready(self, timeout: int = 300) -> bool:
        """Wait for OpenEBS components to be ready."""
        # The deployment name is prefixed with the helm release name
        components = [f"{self.name}-openebs-localpv-provisioner"]

        for component in components:
            print(f"  Waiting for {component}...")
            if not self.k8s.wait_for_deployment(component, self.namespace, timeout):
                return False

        return True

    def _create_storage_classes(self) -> bool:
        """Create enterprise storage classes."""
        storage_classes = [
            {
                "name": "enterprise-standard",
                "tier": "standard",
                "basePath": "/var/openebs/local",
                "isDefault": True
            },
            {
                "name": "enterprise-ssd",
                "tier": "ssd",
                "basePath": "/var/openebs/ssd",
                "isDefault": False
            },
            {
                "name": "enterprise-fast",
                "tier": "fast",
                "basePath": "/var/openebs/fast",
                "isDefault": False
            }
        ]

        for sc in storage_classes:
            is_default = str(sc['isDefault']).lower()
            manifest = f"""apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: {sc['name']}
  labels:
    compliance.storage/managed-by: enterprise-sim
    compliance.storage/tier: {sc['tier']}
    compliance.storage/encryption: enabled
  annotations:
    storageclass.kubernetes.io/is-default-class: "{is_default}"
provisioner: openebs.io/local
volumeBindingMode: WaitForFirstConsumer
parameters:
  storageType: hostpath
  basePath: "{sc['basePath']}"
reclaimPolicy: Delete
"""

            if not self.k8s.apply_manifest(manifest):
                print(f"ERROR: Failed to create storage class: {sc['name']}")
                return False

            print(f"  Created storage class: {sc['name']} (tier: {sc['tier']})")

        return True

    def _remove_storage_classes(self) -> bool:
        """Remove enterprise storage classes."""
        storage_classes = ["enterprise-standard", "enterprise-ssd", "enterprise-fast"]

        for sc_name in storage_classes:
            manifest = f"""apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: {sc_name}
"""
            self.k8s.delete_manifest(manifest)
            print(f"  Removed storage class: {sc_name}")

        return True

    def _validate_storage_classes(self) -> bool:
        """Validate that enterprise storage classes exist and are working."""
        storage_classes = ["enterprise-standard", "enterprise-ssd", "enterprise-fast"]

        for sc_name in storage_classes:
            sc = self.k8s.get_resource("storageclass", sc_name)
            if not sc:
                return False

        return True

    def validate(self) -> bool:
        """Validate OpenEBS installation and functionality."""
        print(f"Validating {self.name} service...")

        # Check if service is installed
        if not self.is_installed():
            print(f"  [FAIL] Service not installed")
            return False

        print(f"  [PASS] Service is installed")

        # Check OpenEBS deployment
        deployment_name = f"{self.name}-openebs-localpv-provisioner"
        deployment = self.k8s.get_resource("deployment", deployment_name, self.namespace)
        if not deployment:
            print("  [FAIL] OpenEBS LocalPV provisioner deployment not found")
            return False

        print("  [PASS] OpenEBS LocalPV provisioner deployment exists")

        # Validate storage classes
        if not self._validate_storage_classes():
            print("  [FAIL] Enterprise storage classes validation failed")
            return False

        print("  [PASS] Enterprise storage classes available")

        # Check storage class labels
        storage_classes = self.k8s.get_resource("storageclass")
        if storage_classes:
            enterprise_classes = []
            for sc in storage_classes.get("items", []):
                labels = sc.get("metadata", {}).get("labels", {})
                if labels.get("compliance.storage/managed-by") == "enterprise-sim":
                    enterprise_classes.append(sc.get("metadata", {}).get("name"))

            if len(enterprise_classes) >= 3:
                print(f"  [PASS] Enterprise storage classes found: {', '.join(enterprise_classes)}")
            else:
                print(f"  [FAIL] Expected 3+ enterprise storage classes, found: {len(enterprise_classes)}")
                return False

        print(f"  [PASS] {self.name} service validation completed")
        return True