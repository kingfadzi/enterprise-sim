"""cert-manager service implementation for certificate management."""

from typing import Dict, List, Set
from .base import BaseService, ServiceHealth
from ..core.config import ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


class CertManagerService(BaseService):
    """cert-manager certificate management service."""

    @property
    def name(self) -> str:
        return "cert-manager"

    @property
    def namespace(self) -> str:
        return "cert-manager"

    @property
    def dependencies(self) -> Set[str]:
        return set()  # cert-manager has no dependencies

    @property
    def helm_chart(self) -> Dict[str, str]:
        return {
            'repo': 'jetstack',
            'repo_url': 'https://charts.jetstack.io',
            'chart': 'cert-manager'
        }

    def get_helm_values(self) -> Dict:
        """Get Helm values for cert-manager."""
        values = {
            'installCRDs': True,
            'replicaCount': 1,
            'webhook': {
                'replicaCount': 1
            },
            'cainjector': {
                'replicaCount': 1
            },
            'resources': {
                'requests': {
                    'cpu': '10m',
                    'memory': '32Mi'
                }
            }
        }

        # Merge any custom configuration
        if self.config.config:
            values.update(self.config.config)

        return values

    def validate_prerequisites(self) -> bool:
        """Validate cert-manager prerequisites."""
        # cert-manager doesn't have specific prerequisites
        return True

    def post_install_tasks(self) -> bool:
        """Execute cert-manager post-installation tasks."""
        print("Executing cert-manager post-install tasks...")

        # Wait for cert-manager deployments to be ready
        deployments = ['cert-manager', 'cert-manager-webhook', 'cert-manager-cainjector']

        for deployment in deployments:
            print(f"  Waiting for {deployment} to be ready...")
            if not self.k8s.wait_for_deployment(deployment, self.namespace, timeout=300):
                print(f"ERROR: {deployment} deployment not ready")
                return False

        # Verify CRDs are installed
        if not self._verify_crds():
            print("ERROR: cert-manager CRDs not properly installed")
            return False

        # Test cert-manager webhook
        if not self._test_webhook():
            print("WARNING: cert-manager webhook test failed")
            # Don't fail installation for webhook test failure

        print("cert-manager post-install tasks completed")
        return True

    def _verify_crds(self) -> bool:
        """Verify cert-manager CRDs are installed."""
        required_crds = [
            'certificates.cert-manager.io',
            'certificaterequests.cert-manager.io',
            'issuers.cert-manager.io',
            'clusterissuers.cert-manager.io'
        ]

        try:
            for crd_name in required_crds:
                crd = self.k8s.get_resource('customresourcedefinitions', crd_name)
                if not crd:
                    print(f"ERROR: CRD not found: {crd_name}")
                    return False

            print("All cert-manager CRDs verified")
            return True

        except Exception as e:
            print(f"Error verifying CRDs: {e}")
            return False

    def _test_webhook(self) -> bool:
        """Test cert-manager webhook functionality."""
        try:
            # Create a test ClusterIssuer to verify webhook
            test_issuer = """
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: test-selfsigned-issuer
spec:
  selfSigned: {}
"""

            if not self.k8s.apply_manifest(test_issuer):
                return False

            # Check if issuer was created successfully
            import time
            time.sleep(5)

            issuer = self.k8s.get_resource('clusterissuers', 'test-selfsigned-issuer')
            if issuer:
                # Clean up test issuer
                self.k8s.delete_manifest(test_issuer)
                print("cert-manager webhook test passed")
                return True
            else:
                return False

        except Exception as e:
            print(f"Webhook test error: {e}")
            return False

    def get_health(self) -> ServiceHealth:
        """Get cert-manager health status."""
        try:
            deployments = ['cert-manager', 'cert-manager-webhook', 'cert-manager-cainjector']
            all_healthy = True
            degraded = False

            for deployment_name in deployments:
                deployment = self.k8s.get_resource('deployment', deployment_name, self.namespace)
                if not deployment:
                    return ServiceHealth.UNHEALTHY

                status = deployment.get('status', {})
                ready_replicas = status.get('readyReplicas', 0)
                replicas = status.get('replicas', 0)

                if ready_replicas == 0:
                    all_healthy = False
                elif ready_replicas < replicas:
                    degraded = True

            if all_healthy and not degraded:
                return ServiceHealth.HEALTHY
            elif not all_healthy:
                return ServiceHealth.UNHEALTHY
            else:
                return ServiceHealth.DEGRADED

        except Exception:
            return ServiceHealth.UNKNOWN

    def get_endpoints(self, domain: str) -> List[Dict[str, str]]:
        """Get cert-manager service endpoints."""
        endpoints = []

        try:
            # cert-manager doesn't expose external endpoints typically
            # but we can provide information about its webhook endpoint
            webhook_service = self.k8s.get_resource('service', 'cert-manager-webhook', self.namespace)
            if webhook_service:
                endpoints.append({
                    'name': 'cert-manager Webhook',
                    'url': f'https://cert-manager-webhook.{self.namespace}.svc.cluster.local:443',
                    'type': 'Internal ClusterIP'
                })

        except Exception as e:
            print(f"Error getting cert-manager endpoints: {e}")

        return endpoints

    def _is_installed_custom(self) -> bool:
        """Check if cert-manager is installed."""
        # Check for cert-manager deployment
        deployment = self.k8s.get_resource('deployment', 'cert-manager', self.namespace)
        return bool(deployment)

    def get_certificate_info(self) -> Dict:
        """Get information about certificates managed by cert-manager."""
        try:
            certificates = self.k8s.get_resource('certificates', namespace='istio-system')
            if not certificates:
                return {'certificates': []}

            cert_list = []
            for cert in certificates.get('items', []):
                cert_info = {
                    'name': cert.get('metadata', {}).get('name'),
                    'namespace': cert.get('metadata', {}).get('namespace'),
                    'secret_name': cert.get('spec', {}).get('secretName'),
                    'dns_names': cert.get('spec', {}).get('dnsNames', []),
                    'issuer': cert.get('spec', {}).get('issuerRef', {}).get('name'),
                    'ready': False
                }

                # Check certificate status
                status = cert.get('status', {})
                conditions = status.get('conditions', [])
                for condition in conditions:
                    if condition.get('type') == 'Ready':
                        cert_info['ready'] = condition.get('status') == 'True'
                        break

                cert_list.append(cert_info)

            return {'certificates': cert_list}

        except Exception as e:
            print(f"Error getting certificate info: {e}")
            return {'certificates': [], 'error': str(e)}

    def get_issuers_info(self) -> Dict:
        """Get information about cert-manager issuers."""
        try:
            # Get ClusterIssuers
            cluster_issuers = self.k8s.get_resource('clusterissuers')
            issuers_list = []

            if cluster_issuers:
                for issuer in cluster_issuers.get('items', []):
                    issuer_info = {
                        'name': issuer.get('metadata', {}).get('name'),
                        'type': 'ClusterIssuer',
                        'ready': False
                    }

                    # Check issuer status
                    status = issuer.get('status', {})
                    conditions = status.get('conditions', [])
                    for condition in conditions:
                        if condition.get('type') == 'Ready':
                            issuer_info['ready'] = condition.get('status') == 'True'
                            break

                    issuers_list.append(issuer_info)

            return {'issuers': issuers_list}

        except Exception as e:
            print(f"Error getting issuers info: {e}")
            return {'issuers': [], 'error': str(e)}

    def uninstall(self) -> bool:
        """Uninstall cert-manager."""
        print("Uninstalling cert-manager...")

        try:
            # Uninstall Helm release
            if not self.helm.uninstall(self.name, self.namespace):
                print("WARNING: Failed to uninstall cert-manager Helm release")

            # Optionally clean up CRDs (dangerous - can break other cert-manager instances)
            # We'll leave CRDs in place by default for safety

            print("cert-manager uninstalled")
            return True

        except Exception as e:
            print(f"ERROR: cert-manager uninstallation failed: {e}")
            return False