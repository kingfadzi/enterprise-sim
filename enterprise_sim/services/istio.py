"""Istio service mesh implementation."""

import subprocess
import time
from typing import Dict, List, Set
from kubernetes.client.exceptions import ApiException
from .base import BaseService, ServiceHealth
from ..core.config import ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


class IstioService(BaseService):
    """Istio service mesh management."""

    @property
    def name(self) -> str:
        return "istio"

    @property
    def namespace(self) -> str:
        return "istio-system"

    @property
    def dependencies(self) -> Set[str]:
        return set()  # Istio has no dependencies

    @property
    def helm_chart(self) -> Dict[str, str]:
        return None

    def get_helm_values(self) -> Dict:
        """Get Helm values for Istio."""
        values = {
            'pilot': {
                'traceSampling': 1.0
            },
            'global': {
                'meshID': 'mesh1',
                'network': 'network1'
            }
        }

        # Merge any custom configuration
        if self.config.config:
            values.update(self.config.config)

        return values

    def validate_prerequisites(self) -> bool:
        """Validate Istio prerequisites."""
        # Check if istioctl is available
        try:
            result = subprocess.run(['istioctl', 'version', '--short'],
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                print("❌ istioctl not found or not working")
                return False
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("❌ istioctl not found in PATH")
            return False

        return True

    def install(self) -> bool:
        """Install Istio service mesh."""
        if not self.config.enabled:
            print("Istio is disabled, skipping installation")
            return True

        print("Installing Istio service mesh...")

        try:
            # Install Istio base
            if not self._install_base():
                return False

            # Install Istiod (control plane)
            if not self._install_istiod():
                return False

            # Install Istio ingress gateway
            if not self._install_gateway():
                return False

            return True

        except Exception as e:
            print(f"❌ Istio installation failed: {e}")
            return False

    def _install_base(self) -> bool:
        """Install Istio base components."""
        print("📦 Installing Istio base...")

        # Add Istio Helm repository
        if not self.helm.add_repo('istio', 'https://istio-release.storage.googleapis.com/charts'):
            return False

        if not self.helm.update_repos():
            return False

        # Install base
        return self.helm.install(
            release_name='istio-base',
            chart='istio/base',
            namespace=self.namespace,
            version=self.config.version if self.config.version != 'latest' else None
        )

    def _install_istiod(self) -> bool:
        """Install Istiod control plane."""
        print("📦 Installing Istiod...")

        values = self.get_helm_values()
        return self.helm.install(
            release_name='istiod',
            chart='istio/istiod',
            namespace=self.namespace,
            values=values,
            version=self.config.version if self.config.version != 'latest' else None
        )

    def _install_gateway(self) -> bool:
        """Install Istio ingress gateway."""
        print("📦 Installing Istio gateway...")

        gateway_values = {
            'service': {
                'type': 'LoadBalancer'
            }
        }

        return self.helm.install(
            release_name='istio-ingressgateway',
            chart='istio/gateway',
            namespace=self.namespace,
            values=gateway_values,
            version=self.config.version if self.config.version != 'latest' else None
        )

    def post_install_tasks(self) -> bool:
        """Execute Istio post-installation tasks."""
        print("🔧 Executing Istio post-install tasks...")

        # Wait for Istiod to be ready
        if not self.k8s.wait_for_deployment('istiod', self.namespace, timeout=300):
            print("❌ Istiod deployment not ready")
            return False

        # Wait for ingress gateway to be ready
        if not self.k8s.wait_for_deployment('istio-ingressgateway', self.namespace, timeout=300):
            print("❌ Istio ingress gateway not ready")
            return False

        # Verify installation with istioctl
        if not self._verify_installation():
            print("❌ Istio installation verification failed")
            return False

        print("✅ Istio post-install tasks completed")
        return True

    def _verify_installation(self) -> bool:
        """Verify Istio installation."""
        try:
            # Check istioctl version
            result = subprocess.run([
                'istioctl', 'version', '--short'
            ], check=True, capture_output=True, text=True, timeout=30)

            print(f"Istio version: {result.stdout.strip()}")

            # Run istioctl verify-install
            result = subprocess.run([
                'istioctl', 'verify-install'
            ], check=True, capture_output=True, text=True, timeout=60)

            return True

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"Istio verification failed: {e}")
            return False

    def get_health(self) -> ServiceHealth:
        """Get Istio health status."""
        try:
            # Check if Istiod deployment is ready
            deployment = self.k8s.get_resource('deployment', 'istiod', self.namespace)
            if not deployment:
                return ServiceHealth.UNKNOWN

            status = deployment.get('status', {})
            ready_replicas = status.get('readyReplicas', 0)
            replicas = status.get('replicas', 0)

            if ready_replicas == replicas and replicas > 0:
                # Check ingress gateway
                gateway_deployment = self.k8s.get_resource('deployment', 'istio-ingressgateway', self.namespace)
                if gateway_deployment:
                    gateway_status = gateway_deployment.get('status', {})
                    gateway_ready = gateway_status.get('readyReplicas', 0)
                    gateway_replicas = gateway_status.get('replicas', 0)

                    if gateway_ready == gateway_replicas and gateway_replicas > 0:
                        return ServiceHealth.HEALTHY
                    else:
                        return ServiceHealth.DEGRADED

            return ServiceHealth.UNHEALTHY

        except Exception:
            return ServiceHealth.UNKNOWN

    def get_endpoints(self, domain: str) -> List[Dict[str, str]]:
        """Get Istio service endpoints."""
        endpoints = []

        try:
            # Get ingress gateway service
            service = self.k8s.get_resource('service', 'istio-ingressgateway', self.namespace)
            if service:
                status = service.get('status', {})
                load_balancer = status.get('loadBalancer', {})
                ingress = load_balancer.get('ingress', [])

                if ingress:
                    for ing in ingress:
                        ip = ing.get('ip')
                        hostname = ing.get('hostname')

                        if ip:
                            endpoints.append({
                                'name': 'Istio Ingress Gateway',
                                'url': f'http://{ip}',
                                'type': 'LoadBalancer IP'
                            })
                        elif hostname:
                            endpoints.append({
                                'name': 'Istio Ingress Gateway',
                                'url': f'http://{hostname}',
                                'type': 'LoadBalancer Hostname'
                            })
                else:
                    # For k3d, the service will be available on configured domain with mapped ports
                    domain = self._get_domain()
                    http_port = self._get_http_port()
                    https_port = self._get_https_port()

                    endpoints.append({
                        'name': 'Istio Ingress Gateway (HTTP)',
                        'url': f'http://{domain}:{http_port}',
                        'type': 'k3d Port Mapping'
                    })
                    endpoints.append({
                        'name': 'Istio Ingress Gateway (HTTPS)',
                        'url': f'https://{domain}:{https_port}',
                        'type': 'k3d Port Mapping'
                    })

        except Exception as e:
            print(f"Error getting Istio endpoints: {e}")

        return endpoints

    def _get_domain(self) -> str:
        """Get domain from config."""
        from ..core.config import ConfigManager
        config_manager = ConfigManager('config.yaml')
        domain = config_manager.config.environment.get('domain')
        if domain is None:
            raise ValueError("Domain not configured in environment settings")
        return domain

    def _get_http_port(self) -> int:
        """Get HTTP port from cluster config."""
        from ..core.config import ConfigManager
        config_manager = ConfigManager('config.yaml')
        cluster_config = config_manager.get_cluster_config()
        return cluster_config.ingress_http_port

    def _get_https_port(self) -> int:
        """Get HTTPS port from cluster config."""
        from ..core.config import ConfigManager
        config_manager = ConfigManager('config.yaml')
        cluster_config = config_manager.get_cluster_config()
        return cluster_config.ingress_https_port

    def _is_installed_custom(self) -> bool:
        """Check if Istio is installed."""
        # Check for Istio deployments
        istiod = self.k8s.get_resource('deployment', 'istiod', self.namespace)
        gateway = self.k8s.get_resource('deployment', 'istio-ingressgateway', self.namespace)

        return bool(istiod and gateway)

    def uninstall(self) -> bool:
        """Uninstall Istio."""
        print("🗑️  Uninstalling Istio...")

        try:
            # Uninstall in reverse order
            releases = ['istio-ingressgateway', 'istiod', 'istio-base']

            for release in releases:
                if not self.helm.uninstall(release, self.namespace):
                    print(f"⚠️  Failed to uninstall {release}")

            # Clean up CRDs (optional - can be dangerous)
            # self._cleanup_crds()

            print("✅ Istio uninstalled")
            return True

        except Exception as e:
            print(f"❌ Istio uninstallation failed: {e}")
            return False

    def _cleanup_crds(self) -> bool:
        """Clean up Istio CRDs (use with caution)."""
        try:
            if not self.k8s.apiextensions_v1:
                subprocess.run([
                    'kubectl', 'delete', 'crd', '-l', 'app=istio-pilot'
                ], check=True, capture_output=True)
                return True

            crds = self.k8s.apiextensions_v1.list_custom_resource_definition(
                label_selector='app=istio-pilot'
            )
            for crd in crds.items:
                name = crd.metadata.name
                self.k8s.apiextensions_v1.delete_custom_resource_definition(name)
            return True
        except subprocess.CalledProcessError:
            return False
        except ApiException as exc:
            print(f"Failed to delete Istio CRDs via API: {exc}")
            return False
