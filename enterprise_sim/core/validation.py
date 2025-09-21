"""Service validation framework."""

import subprocess
import time
from typing import Dict, List, Optional, Tuple
from ..utils.k8s import KubernetesClient
from ..utils.manifests import render_manifest
from kubernetes.client.exceptions import ApiException


class ValidationResult:
    """Result of a validation check."""

    def __init__(self, name: str, success: bool, message: str, details: Optional[str] = None):
        self.name = name
        self.success = success
        self.message = message
        self.details = details

    def __str__(self):
        status = "PASS" if self.success else "FAIL"
        result = f"[{status}] {self.name}: {self.message}"
        if self.details:
            result += f"\n        Details: {self.details}"
        return result


class ServiceValidator:
    """Framework for validating services and infrastructure."""

    def __init__(self, k8s_client: KubernetesClient):
        self.k8s = k8s_client

    def validate_cluster_basics(self) -> List[ValidationResult]:
        """Validate basic cluster functionality."""
        results = []

        # Check kubectl connectivity
        results.append(self._check_kubectl_connectivity())

        # Check node readiness
        results.append(self._check_node_readiness())

        # Check system pods
        results.append(self._check_system_pods())

        # Check DNS functionality
        results.append(self._check_dns_functionality())

        return results

    def validate_service_deployment(self, service_name: str, namespace: str) -> List[ValidationResult]:
        """Validate a service deployment."""
        results = []

        # Check namespace exists
        results.append(self._check_namespace_exists(namespace))

        # Check deployment status
        results.append(self._check_deployment_status(service_name, namespace))

        # Check pod readiness
        results.append(self._check_pod_readiness(service_name, namespace))

        # Check service endpoints
        results.append(self._check_service_endpoints(service_name, namespace))

        return results

    def validate_istio_mesh(self) -> List[ValidationResult]:
        """Validate Istio service mesh."""
        results = []

        # Check Istio installation
        results.append(self._check_istio_installation())

        # Check Istiod health
        results.append(self._check_istiod_health())

        # Check ingress gateway
        results.append(self._check_istio_gateway())

        # Check mTLS configuration
        results.append(self._check_mtls_configuration())

        return results

    def validate_network_policies(self, namespace: str) -> List[ValidationResult]:
        """Validate network policies."""
        results = []

        # Check network policy exists
        results.append(self._check_network_policy_exists(namespace))

        # Test DNS connectivity
        results.append(self._test_dns_connectivity(namespace))

        # Test Istio connectivity
        results.append(self._test_istio_connectivity(namespace))

        return results

    def _check_kubectl_connectivity(self) -> ValidationResult:
        """Check kubectl connectivity."""
        try:
            # This checks if the API server is reachable.
            self.k8s.core_v1.get_api_resources()
            return ValidationResult(
                "Kubernetes API Connectivity",
                True,
                "Cluster is accessible",
                "API server responded successfully"
            )
        except ApiException as e:
            return ValidationResult(
                "Kubernetes API Connectivity",
                False,
                "Cannot connect to cluster",
                str(e)
            )

    def _check_node_readiness(self) -> ValidationResult:
        """Check if all nodes are ready."""
        try:
            nodes = self.k8s.get_resource('nodes')
            if not nodes or 'items' not in nodes:
                return ValidationResult(
                    "Node Readiness",
                    False,
                    "No nodes found",
                    "kubectl get nodes returned no results"
                )

            total_nodes = len(nodes['items'])
            ready_nodes = 0

            for node in nodes['items']:
                conditions = node.get('status', {}).get('conditions', [])
                for condition in conditions:
                    if condition.get('type') == 'Ready' and condition.get('status') == 'True':
                        ready_nodes += 1
                        break

            if ready_nodes == total_nodes:
                return ValidationResult(
                    "Node Readiness",
                    True,
                    f"All {total_nodes} nodes are ready",
                    f"{ready_nodes}/{total_nodes} nodes ready"
                )
            else:
                return ValidationResult(
                    "Node Readiness",
                    False,
                    f"Only {ready_nodes}/{total_nodes} nodes are ready",
                    "Some nodes are not ready"
                )

        except Exception as e:
            return ValidationResult(
                "Node Readiness",
                False,
                "Failed to check node status",
                str(e)
            )

    def _check_system_pods(self) -> ValidationResult:
        """Check system pod health."""
        try:
            pods = self.k8s.get_pods('kube-system')
            if not pods:
                return ValidationResult(
                    "System Pods",
                    False,
                    "No system pods found",
                    "kubectl get pods -n kube-system returned no results"
                )

            total_pods = len(pods)
            running_pods = 0

            for pod in pods:
                if pod.get('status', {}).get('phase') == 'Running':
                    running_pods += 1

            if running_pods == total_pods:
                return ValidationResult(
                    "System Pods",
                    True,
                    f"All {total_pods} system pods are running",
                    f"{running_pods}/{total_pods} pods running"
                )
            else:
                return ValidationResult(
                    "System Pods",
                    False,
                    f"Only {running_pods}/{total_pods} system pods are running",
                    "Some system pods are not running"
                )

        except Exception as e:
            return ValidationResult(
                "System Pods",
                False,
                "Failed to check system pods",
                str(e)
            )

    def _check_dns_functionality(self) -> ValidationResult:
        """Check DNS functionality."""
        try:
            # Create a test pod to check DNS
            test_pod_manifest = render_manifest("manifests/validation/dns-test-pod.yaml")

            if not self.k8s.apply_manifest(test_pod_manifest):
                return ValidationResult(
                    "DNS Functionality",
                    False,
                    "Failed to create DNS test pod",
                    "Could not create test pod"
                )

            # Wait for pod to be ready
            time.sleep(5)

            # Test DNS resolution
            dns_result = self.k8s.execute_in_pod(
                'dns-test',
                ['nslookup', 'kubernetes.default.svc.cluster.local'],
                'default'
            )

            # Clean up test pod
            self.k8s.delete_manifest(test_pod_manifest)

            if dns_result and 'Name:' in dns_result:
                return ValidationResult(
                    "DNS Functionality",
                    True,
                    "DNS resolution is working",
                    "kubernetes.default.svc.cluster.local resolves correctly"
                )
            else:
                return ValidationResult(
                    "DNS Functionality",
                    False,
                    "DNS resolution failed",
                    "Cannot resolve kubernetes.default.svc.cluster.local"
                )

        except Exception as e:
            return ValidationResult(
                "DNS Functionality",
                False,
                "DNS test failed",
                str(e)
            )

    def _check_namespace_exists(self, namespace: str) -> ValidationResult:
        """Check if namespace exists."""
        try:
            ns = self.k8s.get_resource('namespace', namespace)
            if ns:
                return ValidationResult(
                    f"Namespace {namespace}",
                    True,
                    "Namespace exists",
                    f"Namespace {namespace} is present"
                )
            else:
                return ValidationResult(
                    f"Namespace {namespace}",
                    False,
                    "Namespace not found",
                    f"Namespace {namespace} does not exist"
                )
        except Exception as e:
            return ValidationResult(
                f"Namespace {namespace}",
                False,
                "Failed to check namespace",
                str(e)
            )

    def _check_deployment_status(self, service_name: str, namespace: str) -> ValidationResult:
        """Check deployment status."""
        try:
            deployment = self.k8s.get_resource('deployment', service_name, namespace)
            if not deployment:
                return ValidationResult(
                    f"Deployment {service_name}",
                    False,
                    "Deployment not found",
                    f"Deployment {service_name} does not exist in {namespace}"
                )

            status = deployment.get('status', {})
            ready_replicas = status.get('readyReplicas', 0)
            replicas = status.get('replicas', 0)

            if ready_replicas == replicas and replicas > 0:
                return ValidationResult(
                    f"Deployment {service_name}",
                    True,
                    "Deployment is ready",
                    f"{ready_replicas}/{replicas} replicas ready"
                )
            else:
                return ValidationResult(
                    f"Deployment {service_name}",
                    False,
                    "Deployment not ready",
                    f"Only {ready_replicas}/{replicas} replicas ready"
                )

        except Exception as e:
            return ValidationResult(
                f"Deployment {service_name}",
                False,
                "Failed to check deployment",
                str(e)
            )

    def _check_pod_readiness(self, service_name: str, namespace: str) -> ValidationResult:
        """Check pod readiness."""
        try:
            pods = self.k8s.get_pods(namespace, f'app={service_name}')
            if not pods:
                return ValidationResult(
                    f"Pods {service_name}",
                    False,
                    "No pods found",
                    f"No pods with label app={service_name}"
                )

            ready_pods = 0
            for pod in pods:
                conditions = pod.get('status', {}).get('conditions', [])
                for condition in conditions:
                    if condition.get('type') == 'Ready' and condition.get('status') == 'True':
                        ready_pods += 1
                        break

            total_pods = len(pods)
            if ready_pods == total_pods:
                return ValidationResult(
                    f"Pods {service_name}",
                    True,
                    "All pods are ready",
                    f"{ready_pods}/{total_pods} pods ready"
                )
            else:
                return ValidationResult(
                    f"Pods {service_name}",
                    False,
                    "Some pods not ready",
                    f"{ready_pods}/{total_pods} pods ready"
                )

        except Exception as e:
            return ValidationResult(
                f"Pods {service_name}",
                False,
                "Failed to check pods",
                str(e)
            )

    def _check_service_endpoints(self, service_name: str, namespace: str) -> ValidationResult:
        """Check service endpoints."""
        try:
            service = self.k8s.get_resource('service', service_name, namespace)
            if not service:
                return ValidationResult(
                    f"Service {service_name}",
                    False,
                    "Service not found",
                    f"Service {service_name} does not exist"
                )

            endpoints = self.k8s.get_resource('endpoints', service_name, namespace)
            if not endpoints:
                return ValidationResult(
                    f"Service Endpoints {service_name}",
                    False,
                    "No endpoints found",
                    "Service has no endpoints"
                )

            subsets = endpoints.get('subsets', [])
            if subsets and any(subset.get('addresses') for subset in subsets):
                return ValidationResult(
                    f"Service Endpoints {service_name}",
                    True,
                    "Service has healthy endpoints",
                    "Endpoints are available"
                )
            else:
                return ValidationResult(
                    f"Service Endpoints {service_name}",
                    False,
                    "Service has no healthy endpoints",
                    "No ready addresses in endpoints"
                )

        except Exception as e:
            return ValidationResult(
                f"Service Endpoints {service_name}",
                False,
                "Failed to check service endpoints",
                str(e)
            )

    def _check_istio_installation(self) -> ValidationResult:
        """Check Istio installation."""
        try:
            result = subprocess.run([
                'istioctl', 'version', '--short'
            ], check=True, capture_output=True, text=True, timeout=10)

            return ValidationResult(
                "Istio Installation",
                True,
                "Istio is installed",
                result.stdout.strip()
            )
        except Exception as e:
            return ValidationResult(
                "Istio Installation",
                False,
                "Istio not found or not working",
                str(e)
            )

    def _check_istiod_health(self) -> ValidationResult:
        """Check Istiod health."""
        return self._check_deployment_status('istiod', 'istio-system')

    def _check_istio_gateway(self) -> ValidationResult:
        """Check Istio ingress gateway."""
        return self._check_deployment_status('istio-ingressgateway', 'istio-system')

    def _check_mtls_configuration(self) -> ValidationResult:
        """Check mTLS configuration."""
        # This is a placeholder - in real implementation you'd check PeerAuthentication policies
        return ValidationResult(
            "mTLS Configuration",
            True,
            "mTLS check not implemented",
            "Placeholder validation"
        )

    def _check_network_policy_exists(self, namespace: str) -> ValidationResult:
        """Check if network policy exists."""
        try:
            policies = self.k8s.get_resource('networkpolicies', namespace=namespace)
            if policies and policies.get('items'):
                return ValidationResult(
                    f"Network Policies {namespace}",
                    True,
                    "Network policies found",
                    f"{len(policies['items'])} policies in namespace"
                )
            else:
                return ValidationResult(
                    f"Network Policies {namespace}",
                    False,
                    "No network policies found",
                    "No network policies in namespace"
                )
        except Exception as e:
            return ValidationResult(
                f"Network Policies {namespace}",
                False,
                "Failed to check network policies",
                str(e)
            )

    def _test_dns_connectivity(self, namespace: str) -> ValidationResult:
        """Test DNS connectivity from namespace."""
        # Placeholder - would create test pod and verify DNS resolution
        return ValidationResult(
            f"DNS Connectivity {namespace}",
            True,
            "DNS connectivity check not implemented",
            "Placeholder validation"
        )

    def _test_istio_connectivity(self, namespace: str) -> ValidationResult:
        """Test Istio sidecar connectivity."""
        # Placeholder - would verify Istio proxy connectivity
        return ValidationResult(
            f"Istio Connectivity {namespace}",
            True,
            "Istio connectivity check not implemented",
            "Placeholder validation"
        )
