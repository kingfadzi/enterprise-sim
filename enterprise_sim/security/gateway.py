"""Istio ingress gateway management for enterprise simulation."""

import time
from typing import Dict, List, Optional
import yaml
from ..utils.k8s import KubernetesClient
from ..utils.manifests import load_single_manifest, render_manifest


class GatewayManager:
    """Manages Istio ingress gateway configuration."""

    def __init__(self, k8s_client: KubernetesClient, domain: str = "localhost"):
        self.k8s = k8s_client
        self.domain = domain
        self.wildcard_domain = f"*.{domain}"
        self.gateway_name = f"{domain.replace('.', '-')}-gateway"
        self.secret_name = f"{domain.replace('.', '-')}-tls"

    def create_wildcard_gateway(self) -> bool:
        """Create shared wildcard gateway for the simulation environment."""
        print(f"Creating wildcard gateway: {self.gateway_name}")
        print(f"  Domain: {self.wildcard_domain}")
        print(f"  TLS Secret: {self.secret_name}")

        # Verify TLS secret exists
        if not self._verify_tls_secret():
            print("ERROR: TLS secret not found. Setup certificates first.")
            print(f"       Run: enterprise-sim security setup-certificates")
            return False

        # Create the gateway
        try:
            body = load_single_manifest(
                "manifests/gateway/wildcard-gateway.yaml",
                gateway_name=self.gateway_name,
                domain=self.domain,
                secret_name=self.secret_name,
            )
            self.k8s.custom_objects.create_namespaced_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                namespace="istio-system",
                plural="gateways",
                body=body,
            )
            print("Wildcard gateway created successfully")
            return True
        except Exception as e:
            if "AlreadyExists" in str(e):
                print("Wildcard gateway already exists")
                return True
            print(f"ERROR: Failed to create wildcard gateway: {e}")
            return False

    def _verify_tls_secret(self) -> bool:
        """Verify that the TLS secret exists."""
        try:
            secret = self.k8s.get_resource('secret', self.secret_name, 'istio-system')
            if not secret:
                return False

            # Check that it has the required TLS data
            data = secret.get('data', {})
            return 'tls.crt' in data and 'tls.key' in data

        except Exception:
            return False

    def create_virtual_service(self, app_name: str, region: str, service_name: str,
                              port: int = 80, namespace: Optional[str] = None) -> bool:
        """Create VirtualService for application routing.

        Args:
            app_name: Application name for hostname
            region: Region name (us, eu, ap)
            service_name: Kubernetes service name
            port: Service port
            namespace: Service namespace (defaults to region-{region})
        """
        if not namespace:
            namespace = f"region-{region}"

        hostname = f"{region}-{app_name}.{self.domain}"
        vs_name = f"{app_name}-{region}-vs"

        print(f"Creating VirtualService: {vs_name}")
        print(f"  Hostname: {hostname}")
        print(f"  Target: {service_name}.{namespace}:{port}")

        manifest_text = render_manifest(
            "manifests/routing/virtualservice-basic.yaml",
            vs_name=vs_name,
            namespace=namespace,
            app_name=app_name,
            region=region,
            host=hostname,
            gateway_name=self.gateway_name,
            service_host=f"{service_name}.{namespace}.svc.cluster.local",
            service_port=port,
        )

        if not self.k8s.apply_manifest(manifest_text, namespace):
            print(f"ERROR: Failed to create VirtualService {vs_name}")
            return False

        print(f"VirtualService {vs_name} created successfully")
        return True

    def create_destination_rule(self, service_name: str, namespace: str,
                               versions: List[str] = None) -> bool:
        """Create DestinationRule for service with optional version subsets.

        Args:
            service_name: Kubernetes service name
            namespace: Service namespace
            versions: List of version labels for subsets (e.g., ['v1', 'v2'])
        """
        dr_name = f"{service_name}-dr"

        print(f"Creating DestinationRule: {dr_name}")

        service_host = f"{service_name}.{namespace}.svc.cluster.local"
        manifest_doc = load_single_manifest(
            "manifests/routing/destinationrule-basic.yaml",
            dr_name=dr_name,
            namespace=namespace,
            service_name=service_name,
            service_host=service_host,
        )

        if versions:
            manifest_doc.setdefault('spec', {})['subsets'] = [
                {'name': version, 'labels': {'version': version}}
                for version in versions
            ]

        manifest_text = yaml.dump(manifest_doc)

        if not self.k8s.apply_manifest(manifest_text, namespace):
            print(f"ERROR: Failed to create DestinationRule {dr_name}")
            return False

        print(f"DestinationRule {dr_name} created successfully")
        return True

    def setup_canary_routing(self, app_name: str, region: str, service_name: str,
                            v1_weight: int = 90, v2_weight: int = 10,
                            namespace: Optional[str] = None) -> bool:
        """Setup canary routing between v1 and v2 versions.

        Args:
            app_name: Application name
            region: Region name
            service_name: Kubernetes service name
            v1_weight: Weight percentage for v1 (default 90%)
            v2_weight: Weight percentage for v2 (default 10%)
            namespace: Service namespace
        """
        if not namespace:
            namespace = f"region-{region}"

        if v1_weight + v2_weight != 100:
            print("ERROR: Version weights must sum to 100")
            return False

        hostname = f"{region}-{app_name}.{self.domain}"
        vs_name = f"{app_name}-{region}-vs"

        print(f"Setting up canary routing for {hostname}")
        print(f"  v1: {v1_weight}%, v2: {v2_weight}%")

        # First create DestinationRule with v1/v2 subsets
        if not self.create_destination_rule(service_name, namespace, ['v1', 'v2']):
            return False

        service_host = f"{service_name}.{namespace}.svc.cluster.local"
        manifest_text = render_manifest(
            "manifests/routing/virtualservice-canary.yaml",
            vs_name=vs_name,
            namespace=namespace,
            app_name=app_name,
            region=region,
            host=hostname,
            gateway_name=self.gateway_name,
            service_host=service_host,
            v1_weight=v1_weight,
            v2_weight=v2_weight,
        )

        if not self.k8s.apply_manifest(manifest_text, namespace):
            print(f"ERROR: Failed to create canary VirtualService {vs_name}")
            return False

        print(f"Canary routing configured for {hostname}")
        return True

    def setup_failover_routing(self, app_name: str, primary_region: str,
                              failover_region: str, service_name: str,
                              failover_percentage: int = 20) -> bool:
        """Setup cross-region failover routing.

        Args:
            app_name: Application name
            primary_region: Primary region (e.g., 'us')
            failover_region: Failover region (e.g., 'eu')
            service_name: Service name in both regions
            failover_percentage: Percentage of traffic to send to failover region
        """
        primary_weight = 100 - failover_percentage
        hostname = f"{primary_region}-{app_name}.{self.domain}"
        vs_name = f"{app_name}-{primary_region}-failover-vs"

        print(f"Setting up failover routing for {hostname}")
        print(f"  Primary ({primary_region}): {primary_weight}%")
        print(f"  Failover ({failover_region}): {failover_percentage}%")

        manifest_text = render_manifest(
            "manifests/routing/virtualservice-failover.yaml",
            vs_name=vs_name,
            namespace=f"region-{primary_region}",
            app_name=app_name,
            primary_region=primary_region,
            host=hostname,
            gateway_name=self.gateway_name,
            primary_service_host=f"{service_name}.region-{primary_region}.svc.cluster.local",
            failover_service_host=f"{service_name}.region-{failover_region}.svc.cluster.local",
            primary_weight=primary_weight,
            failover_weight=failover_percentage,
        )

        if not self.k8s.apply_manifest(manifest_text, f"region-{primary_region}"):
            print(f"ERROR: Failed to create failover VirtualService {vs_name}")
            return False

        print(f"Failover routing configured for {hostname}")
        return True

    def get_gateway_status(self) -> Dict:
        """Get status of the wildcard gateway."""
        try:
            gateway = self.k8s.get_resource('gateways', self.gateway_name, 'istio-system')
            if not gateway:
                return {'exists': False}

            # Get ingress gateway service status
            ingress_service = self.k8s.get_resource('service', 'istio-ingressgateway', 'istio-system')
            endpoints = []

            if ingress_service:
                status = ingress_service.get('status', {})
                load_balancer = status.get('loadBalancer', {})
                ingress_list = load_balancer.get('ingress', [])

                for ing in ingress_list:
                    if ing.get('ip'):
                        endpoints.append(f"http://{ing['ip']}")
                    elif ing.get('hostname'):
                        endpoints.append(f"http://{ing['hostname']}")

                # For k3d, add localhost endpoints
                if not endpoints:
                    endpoints = [
                        f"http://localhost:8080",  # These should come from config
                        f"https://localhost:8443"
                    ]

            return {
                'exists': True,
                'name': self.gateway_name,
                'namespace': 'istio-system',
                'hosts': [self.wildcard_domain],
                'tls_secret': self.secret_name,
                'endpoints': endpoints
            }

        except Exception as e:
            return {'exists': False, 'error': str(e)}

    def list_virtual_services(self, namespace: Optional[str] = None) -> List[Dict]:
        """List VirtualServices in namespace or all namespaces."""
        try:
            vs_list = self.k8s.get_resource('virtualservices', namespace=namespace)
            if not vs_list:
                return []

            services = []
            for vs in vs_list.get('items', []):
                metadata = vs.get('metadata', {})
                spec = vs.get('spec', {})

                service_info = {
                    'name': metadata.get('name'),
                    'namespace': metadata.get('namespace'),
                    'hosts': spec.get('hosts', []),
                    'gateways': spec.get('gateways', []),
                    'labels': metadata.get('labels', {})
                }

                # Check if it uses our gateway
                gateway_ref = f"istio-system/{self.gateway_name}"
                if gateway_ref in service_info['gateways']:
                    services.append(service_info)

            return services

        except Exception as e:
            print(f"Error listing VirtualServices: {e}")
            return []

    def validate_gateway_connectivity(self) -> bool:
        """Validate gateway connectivity and configuration."""
        print("Validating gateway connectivity...")

        # Check gateway exists
        gateway_status = self.get_gateway_status()
        if not gateway_status.get('exists'):
            print("ERROR: Wildcard gateway not found")
            return False

        # Check TLS secret
        if not self._verify_tls_secret():
            print("ERROR: TLS secret not found or invalid")
            return False

        # Check ingress gateway pods
        ingress_pods = self.k8s.get_pods('istio-system', 'app=istio-ingressgateway')
        if not ingress_pods:
            print("ERROR: No ingress gateway pods found")
            return False

        ready_pods = 0
        for pod in ingress_pods:
            if pod.get('status', {}).get('phase') == 'Running':
                ready_pods += 1

        if ready_pods == 0:
            print("ERROR: No ingress gateway pods are running")
            return False

        print(f"Gateway validation passed ({ready_pods} gateway pods running)")
        return True

    def cleanup_gateway(self) -> bool:
        """Remove the wildcard gateway and related resources."""
        print("Cleaning up wildcard gateway...")

        try:
            # Delete gateway
            gateway_manifest = f"""
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: {self.gateway_name}
  namespace: istio-system
"""
            self.k8s.delete_manifest(gateway_manifest, 'istio-system')
            print(f"Gateway {self.gateway_name} deleted")

            return True

        except Exception as e:
            print(f"ERROR: Failed to cleanup gateway: {e}")
            return False

    def cleanup_virtual_services(self, namespace: Optional[str] = None) -> bool:
        """Remove VirtualServices that use our gateway."""
        print("Cleaning up VirtualServices...")

        try:
            virtual_services = self.list_virtual_services(namespace)

            for vs in virtual_services:
                vs_name = vs['name']
                vs_namespace = vs['namespace']

                # Delete VirtualService
                vs_manifest = f"""
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: {vs_name}
  namespace: {vs_namespace}
"""
                self.k8s.delete_manifest(vs_manifest, vs_namespace)
                print(f"VirtualService {vs_name} deleted from {vs_namespace}")

            return True

        except Exception as e:
            print(f"ERROR: Failed to cleanup VirtualServices: {e}")
            return False
