"""MinIO object storage service implementation."""

import time
from typing import Dict, Any, Set, List, Optional
from ..services.base import BaseService, ServiceStatus, ServiceHealth, ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


class MinioService(BaseService):
    """MinIO S3-compatible object storage service."""

    def __init__(self, config: ServiceConfig, k8s_client: KubernetesClient, helm_client: HelmClient):
        super().__init__(config, k8s_client, helm_client)

    @property
    def name(self) -> str:
        """Service name."""
        return "minio"

    @property
    def namespace(self) -> str:
        """Default namespace for the service."""
        return "minio-operator"

    @property
    def dependencies(self) -> Set[str]:
        """Set of service names this service depends on."""
        return {"storage"}  # MinIO needs storage for persistent volumes

    @property
    def helm_chart(self) -> Optional[Dict[str, str]]:
        """Helm chart information."""
        return {
            'repo': 'minio-operator',
            'repo_url': 'https://operator.min.io',
            'chart': 'operator'
        }

    def get_helm_values(self) -> Dict:
        """Get Helm values for MinIO operator installation."""
        return {
            "operator": {
                "replicaCount": 1
            },
            "console": {
                "enabled": True,
                "service": {
                    "type": "ClusterIP"
                }
            }
        }

    def validate_prerequisites(self) -> bool:
        """Validate prerequisites before installation."""
        # Check if storage service is available
        try:
            # Verify storage classes exist
            storage_classes = self.k8s.get_resource("storageclass")
            if not storage_classes:
                print("ERROR: No storage classes found")
                return False

            # Check for enterprise storage classes
            enterprise_classes = []
            for sc in storage_classes.get("items", []):
                labels = sc.get("metadata", {}).get("labels", {})
                if labels.get("compliance.storage/managed-by") == "enterprise-sim":
                    enterprise_classes.append(sc.get("metadata", {}).get("name"))

            if not enterprise_classes:
                print("ERROR: No enterprise storage classes found. Please install storage service first.")
                return False

            print(f"Found enterprise storage classes: {', '.join(enterprise_classes)}")
            return True

        except Exception as e:
            print(f"ERROR: Cannot validate storage prerequisites: {e}")
            return False

    def post_install_tasks(self) -> bool:
        """Execute post-installation tasks."""
        try:
            print("Waiting for MinIO operator to be ready...")
            if not self._wait_for_operator_ready():
                print("ERROR: MinIO operator not ready within timeout")
                return False

            print("Creating MinIO tenant...")
            if not self._create_minio_tenant():
                print("ERROR: Failed to create MinIO tenant")
                return False

            print("Waiting for MinIO tenant to be ready...")
            if not self._wait_for_tenant_ready():
                print("ERROR: MinIO tenant not ready within timeout")
                return False

            print("Setting up external access...")
            if not self._setup_external_access():
                print("ERROR: Failed to setup external access")
                return False

            return True

        except Exception as e:
            print(f"ERROR: Exception in post_install_tasks: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_endpoints(self) -> List[Dict[str, str]]:
        """Get service endpoints for external access."""
        endpoints = []

        try:
            # Get domain from environment config
            domain = self._get_domain()

            # MinIO S3 API endpoint
            endpoints.append({
                "name": "MinIO S3 API",
                "url": f"https://s3.{domain}",
                "type": "External S3 API"
            })

            # MinIO Console endpoint
            endpoints.append({
                "name": "MinIO Console",
                "url": f"https://minio-console.{domain}",
                "type": "External Web Console"
            })

            # Internal service endpoints
            minio_service = self.k8s.get_resource("service", "minio", self.namespace)
            if minio_service:
                cluster_ip = minio_service.get("spec", {}).get("clusterIP")
                if cluster_ip:
                    endpoints.append({
                        "name": "MinIO Internal API",
                        "url": f"http://{cluster_ip}:9000",
                        "type": "Internal ClusterIP"
                    })

        except Exception as e:
            print(f"Warning: Could not determine endpoints: {e}")

        return endpoints

    def get_health(self) -> ServiceHealth:
        """Get MinIO service health."""
        try:
            # Check operator deployment
            operator_deployment = self.k8s.get_resource("deployment", "minio-operator", self.namespace)
            if not operator_deployment:
                return ServiceHealth.UNHEALTHY

            # Check operator status
            status = operator_deployment.get("status", {})
            ready_replicas = status.get("readyReplicas", 0)
            replicas = status.get("replicas", 0)

            if ready_replicas != replicas or replicas == 0:
                return ServiceHealth.DEGRADED

            # Check if tenant exists and is healthy (tenant is in minio-system namespace)
            tenant_namespace = "minio-system"
            tenant = self.k8s.get_resource("tenant", "enterprise-sim", tenant_namespace)
            if not tenant:
                return ServiceHealth.DEGRADED

            tenant_status = tenant.get("status", {})
            if tenant_status.get("currentState") == "Initialized":
                return ServiceHealth.HEALTHY
            else:
                return ServiceHealth.DEGRADED

        except Exception:
            return ServiceHealth.UNKNOWN

    def uninstall(self) -> bool:
        """Uninstall MinIO service."""
        print("Uninstalling MinIO object storage service...")

        try:
            # Remove external access configuration
            print("  Removing external access configuration...")
            self._remove_external_access()

            # Remove MinIO tenant
            print("  Removing MinIO tenant...")
            self._remove_minio_tenant()

            # Uninstall MinIO operator Helm chart
            if not self.helm.uninstall(self.name, self.namespace):
                print("ERROR: Failed to uninstall MinIO operator")
                return False

            print("MinIO object storage service uninstalled")
            return True

        except Exception as e:
            print(f"ERROR: Failed to uninstall MinIO: {e}")
            return False

    def validate(self) -> bool:
        """Validate MinIO installation and functionality."""
        print(f"Validating {self.name} service...")

        # Check if service is installed
        if not self.is_installed():
            print("  [FAIL] Service not installed")
            return False

        print("  [PASS] Service is installed")

        # Check MinIO operator deployment
        operator_deployment = self.k8s.get_resource("deployment", "minio-operator", self.namespace)
        if not operator_deployment:
            print("  [FAIL] MinIO operator deployment not found")
            return False

        print("  [PASS] MinIO operator deployment exists")

        # Check MinIO tenant
        tenant = self.k8s.get_resource("tenant", "enterprise-tenant", self.namespace)
        if not tenant:
            print("  [FAIL] MinIO tenant not found")
            return False

        print("  [PASS] MinIO tenant exists")

        # Check tenant status
        tenant_status = tenant.get("status", {})
        if tenant_status.get("currentState") != "Initialized":
            print(f"  [FAIL] MinIO tenant not ready (state: {tenant_status.get('currentState')})")
            return False

        print("  [PASS] MinIO tenant is ready")

        # Check external access
        if not self._validate_external_access():
            print("  [FAIL] External access validation failed")
            return False

        print("  [PASS] External access configured")
        print(f"  [PASS] {self.name} service validation completed")
        return True

    def _get_domain(self) -> str:
        """Get domain from config."""
        # Get domain from environment config
        from ..core.config import ConfigManager
        config_manager = ConfigManager('config.yaml')
        domain = config_manager.config.environment.get('domain')
        if domain is None:
            raise ValueError("Domain not configured in environment settings")
        return domain

    def _wait_for_operator_ready(self, timeout: int = 300) -> bool:
        """Wait for MinIO operator to be ready."""
        return self.k8s.wait_for_deployment("minio-operator", self.namespace, timeout)

    def _create_minio_tenant(self) -> bool:
        """Create MinIO tenant with enterprise configuration."""
        # Based on the bash script implementation
        tenant_namespace = "minio-system"

        # First create the tenant namespace
        self.k8s.create_namespace(tenant_namespace)

        # Label namespace for Istio injection
        self.k8s.label_namespace(tenant_namespace, {"istio-injection": "enabled"})

        storage_size = self.config.config.get("storage_size", "10Gi")

        # Create the tenant manifest based on bash script
        tenant_manifest = f"""---
# MinIO credentials secret - must be created first
apiVersion: v1
kind: Secret
metadata:
  name: minio-credentials
  namespace: {tenant_namespace}
  labels:
    app: minio
    compliance.platform: enterprise-sim
type: Opaque
stringData:
  config.env: |
    export MINIO_ROOT_USER="enterprise-admin"
    export MINIO_ROOT_PASSWORD="enterprise-password-123"
---
# Console secret
apiVersion: v1
kind: Secret
metadata:
  name: console-secret
  namespace: {tenant_namespace}
type: Opaque
stringData:
  CONSOLE_PBKDF_PASSPHRASE: "enterprise-sim-console-secret"
  CONSOLE_PBKDF_SALT: "enterprise-sim-salt"
---
# MinIO Tenant for Enterprise Simulation Platform
apiVersion: minio.min.io/v2
kind: Tenant
metadata:
  name: enterprise-sim
  namespace: {tenant_namespace}
  labels:
    app: minio
    compliance.platform: enterprise-sim
    compliance.service: object-storage
spec:
  # Tenant configuration
  image: quay.io/minio/minio:RELEASE.2024-09-09T16-59-28Z
  configuration:
    name: minio-credentials
  pools:
  - name: pool-0
    servers: 4
    volumesPerServer: 1
    volumeClaimTemplate:
      metadata:
        name: data
      spec:
        accessModes:
        - ReadWriteOnce
        resources:
          requests:
            storage: {storage_size}
        storageClassName: enterprise-standard
  # Security and networking
  requestAutoCert: false  # We'll use Istio mTLS
---
# NetworkPolicy for MinIO
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: minio-netpol
  namespace: {tenant_namespace}
  labels:
    compliance.platform: enterprise-sim
    compliance.security: zero-trust
spec:
  podSelector:
    matchLabels:
      app: minio
  policyTypes:
  - Ingress
  - Egress
  ingress:
  # Allow traffic from Istio ingress gateway
  - from:
    - namespaceSelector:
        matchLabels:
          name: istio-system
  # Allow traffic from region namespaces (apps)
  - from:
    - namespaceSelector:
        matchLabels:
          compliance.region: us
  - from:
    - namespaceSelector:
        matchLabels:
          compliance.region: eu
  - from:
    - namespaceSelector:
        matchLabels:
          compliance.region: ap
  egress:
  # Allow DNS resolution
  - to: []
    ports:
    - protocol: UDP
      port: 53
  # Allow communication within MinIO namespace
  - to:
    - namespaceSelector:
        matchLabels:
          name: minio-system
"""

        return self.k8s.apply_manifest(tenant_manifest, tenant_namespace)

    def _wait_for_tenant_ready(self, timeout: int = 300) -> bool:
        """Wait for MinIO tenant to be ready."""
        start_time = time.time()
        tenant_namespace = "minio-system"

        while time.time() - start_time < timeout:
            try:
                tenant = self.k8s.get_resource("tenant", "enterprise-sim", tenant_namespace)
                if tenant:
                    status = tenant.get("status", {})
                    if status.get("currentState") == "Initialized":
                        return True

                print(f"  Tenant state: {status.get('currentState', 'Unknown')}")
                time.sleep(10)

            except Exception as e:
                print(f"  Error checking tenant status: {e}")
                time.sleep(10)

        return False

    def _setup_external_access(self) -> bool:
        """Setup external access via Istio VirtualService."""
        domain = self._get_domain()
        tenant_namespace = "minio-system"

        # Based on bash script - MinIO external routing
        s3_vs_manifest = f"""apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: minio-external
  namespace: {tenant_namespace}
  labels:
    compliance.routing/enabled: "true"
spec:
  hosts:
  - s3.{domain}
  gateways:
  - istio-system/local-sim-gateway
  http:
  - match:
    - uri:
        prefix: /
    route:
    - destination:
        host: minio.{tenant_namespace}.svc.cluster.local
        port:
          number: 80
"""

        # Console VirtualService based on bash script
        console_vs_manifest = f"""apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: minio-console-external
  namespace: {tenant_namespace}
  labels:
    compliance.routing/enabled: "true"
spec:
  hosts:
  - minio-console.{domain}
  gateways:
  - istio-system/local-sim-gateway
  http:
  - match:
    - uri:
        prefix: /
    route:
    - destination:
        host: enterprise-sim-console.{tenant_namespace}.svc.cluster.local
        port:
          number: 9090
"""

        # Apply VirtualServices
        if not self.k8s.apply_manifest(s3_vs_manifest, tenant_namespace):
            return False

        if not self.k8s.apply_manifest(console_vs_manifest, tenant_namespace):
            return False

        return True

    def _validate_external_access(self) -> bool:
        """Validate external access is configured."""
        try:
            # Check S3 API VirtualService
            s3_vs = self.k8s.get_resource("virtualservice", "minio-s3-api", self.namespace)
            if not s3_vs:
                return False

            # Check Console VirtualService
            console_vs = self.k8s.get_resource("virtualservice", "minio-console", self.namespace)
            if not console_vs:
                return False

            return True

        except Exception:
            return False

    def _remove_external_access(self) -> bool:
        """Remove external access configuration."""
        try:
            # Remove VirtualServices
            self.k8s.delete_manifest(f"""apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: minio-s3-api
  namespace: {self.namespace}
""", self.namespace)

            self.k8s.delete_manifest(f"""apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: minio-console
  namespace: {self.namespace}
""", self.namespace)

            return True

        except Exception as e:
            print(f"Warning: Could not remove external access: {e}")
            return False

    def _remove_minio_tenant(self) -> bool:
        """Remove MinIO tenant."""
        try:
            tenant_manifest = f"""apiVersion: minio.min.io/v2
kind: Tenant
metadata:
  name: enterprise-tenant
  namespace: {self.namespace}
"""
            self.k8s.delete_manifest(tenant_manifest, self.namespace)
            return True

        except Exception as e:
            print(f"Warning: Could not remove tenant: {e}")
            return False