"""Zero-trust network policies and security defaults."""

import time
from typing import Dict, List, Optional
from ..utils.k8s import KubernetesClient


class PolicyManager:
    """Manages zero-trust network policies and security configurations."""

    def __init__(self, k8s_client: KubernetesClient):
        self.k8s = k8s_client

    def setup_region_security(self, regions: List[str]) -> bool:
        """Setup zero-trust security policies for regions.

        Args:
            regions: List of region names (e.g., ['us', 'eu', 'ap'])
        """
        print("Setting up zero-trust security policies for regions")

        success = True
        for region in regions:
            namespace = f"region-{region}"
            print(f"  Configuring security for region: {region}")

            if not self._setup_region_namespace(namespace, region):
                success = False
                continue

            if not self._apply_peer_authentication(namespace):
                success = False
                continue

            if not self._apply_authorization_policy(namespace):
                success = False
                continue

            if not self._apply_network_policy(namespace):
                success = False
                continue

        return success

    def _setup_region_namespace(self, namespace: str, region: str) -> bool:
        """Create and configure region namespace."""
        print(f"    Setting up namespace: {namespace}")

        # Create namespace with labels
        namespace_manifest = f"""
apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
  labels:
    istio-injection: enabled
    compliance.region: {region}
    security.policy: zero-trust
spec: {{}}
"""

        if not self.k8s.apply_manifest(namespace_manifest):
            print(f"ERROR: Failed to create namespace {namespace}")
            return False

        print(f"    Namespace {namespace} configured with zero-trust labels")
        return True

    def _apply_peer_authentication(self, namespace: str) -> bool:
        """Apply STRICT mTLS PeerAuthentication policy."""
        print(f"    Applying STRICT mTLS policy to {namespace}")

        peer_auth_manifest = f"""
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: {namespace}
spec:
  mtls:
    mode: STRICT
"""

        if not self.k8s.apply_manifest(peer_auth_manifest, namespace):
            print(f"ERROR: Failed to apply PeerAuthentication to {namespace}")
            return False

        print(f"    STRICT mTLS enforced in {namespace}")
        return True

    def _apply_authorization_policy(self, namespace: str) -> bool:
        """Apply minimal AuthorizationPolicy allowing ingress."""
        print(f"    Applying authorization policy to {namespace}")

        authz_policy_manifest = f"""
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: allow-ingress-gateway
  namespace: {namespace}
spec:
  rules:
  - from:
    - source:
        principals: ["cluster.local/ns/istio-system/sa/istio-ingressgateway-service-account"]
  - from:
    - source:
        namespaces: ["{namespace}"]
---
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: deny-all
  namespace: {namespace}
spec:
  {{}}
"""

        if not self.k8s.apply_manifest(authz_policy_manifest, namespace):
            print(f"ERROR: Failed to apply AuthorizationPolicy to {namespace}")
            return False

        print(f"    Authorization policies applied to {namespace}")
        return True

    def _apply_network_policy(self, namespace: str) -> bool:
        """Apply baseline NetworkPolicy with zero-trust defaults."""
        print(f"    Applying network policy to {namespace}")

        network_policy_manifest = f"""
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: baseline-zero-trust
  namespace: {namespace}
spec:
  podSelector: {{}}
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: istio-system
    - namespaceSelector:
        matchLabels:
          name: {namespace}
  - from:
    - podSelector: {{}}
  egress:
  # Allow DNS resolution
  - to: []
    ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
  # Allow Istio sidecar communication
  - to:
    - namespaceSelector:
        matchLabels:
          name: istio-system
    ports:
    - protocol: TCP
      port: 15012
  # Allow intra-namespace communication
  - to:
    - namespaceSelector:
        matchLabels:
          name: {namespace}
  # Allow egress to other regions (for cross-region communication)
  - to:
    - namespaceSelector:
        matchLabels:
          security.policy: zero-trust
"""

        if not self.k8s.apply_manifest(network_policy_manifest, namespace):
            print(f"ERROR: Failed to apply NetworkPolicy to {namespace}")
            return False

        print(f"    Zero-trust network policy applied to {namespace}")
        return True

    def setup_istio_system_policies(self) -> bool:
        """Setup security policies for istio-system namespace."""
        print("Setting up security policies for istio-system namespace")

        # Label istio-system namespace
        if not self._label_istio_system():
            return False

        # Apply istio-system network policy
        return self._apply_istio_system_network_policy()

    def _label_istio_system(self) -> bool:
        """Add security labels to istio-system namespace."""
        try:
            # Get current namespace
            namespace = self.k8s.get_resource('namespace', 'istio-system')
            if not namespace:
                print("ERROR: istio-system namespace not found")
                return False

            # Apply labels
            label_patch = """
{
  "metadata": {
    "labels": {
      "name": "istio-system",
      "security.policy": "system"
    }
  }
}
"""

            # Use kubectl patch since we don't have a direct patch method
            import subprocess
            result = subprocess.run([
                'kubectl', 'patch', 'namespace', 'istio-system',
                '--type=merge', '-p', label_patch
            ], check=True, capture_output=True)

            print("istio-system namespace labeled for security policies")
            return True

        except Exception as e:
            print(f"ERROR: Failed to label istio-system namespace: {e}")
            return False

    def _apply_istio_system_network_policy(self) -> bool:
        """Apply network policy for istio-system namespace."""
        print("Applying network policy to istio-system")

        istio_network_policy = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: istio-system-policy
  namespace: istio-system
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
  ingress:
  # Allow ingress from everywhere for gateway
  - from: []
    to:
    - podSelector:
        matchLabels:
          app: istio-ingressgateway
  # Allow ingress from application namespaces to istiod
  - from:
    - namespaceSelector:
        matchLabels:
          istio-injection: enabled
    to:
    - podSelector:
        matchLabels:
          app: istiod
    ports:
    - protocol: TCP
      port: 15010
    - protocol: TCP
      port: 15011
    - protocol: TCP
      port: 15012
  egress:
  # Allow DNS resolution
  - to: []
    ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
  # Allow egress to Kubernetes API server
  - to: []
    ports:
    - protocol: TCP
      port: 443
    - protocol: TCP
      port: 6443
  # Allow egress to application namespaces
  - to:
    - namespaceSelector:
        matchLabels:
          istio-injection: enabled
"""

        if not self.k8s.apply_manifest(istio_network_policy, 'istio-system'):
            print("ERROR: Failed to apply istio-system network policy")
            return False

        print("istio-system network policy applied")
        return True

    def validate_policies(self, regions: List[str]) -> bool:
        """Validate that security policies are properly applied."""
        print("Validating security policies...")

        all_valid = True

        # Validate istio-system policies
        if not self._validate_istio_system_policies():
            all_valid = False

        # Validate region policies
        for region in regions:
            namespace = f"region-{region}"
            if not self._validate_region_policies(namespace, region):
                all_valid = False

        return all_valid

    def _validate_istio_system_policies(self) -> bool:
        """Validate istio-system security policies."""
        print("  Validating istio-system policies...")

        try:
            # Check namespace labels
            namespace = self.k8s.get_resource('namespace', 'istio-system')
            if not namespace:
                print("    ERROR: istio-system namespace not found")
                return False

            labels = namespace.get('metadata', {}).get('labels', {})
            if labels.get('name') != 'istio-system':
                print("    ERROR: istio-system namespace missing 'name' label")
                return False

            # Check network policy exists
            network_policy = self.k8s.get_resource('networkpolicies', 'istio-system-policy', 'istio-system')
            if not network_policy:
                print("    ERROR: istio-system network policy not found")
                return False

            print("    istio-system policies validated")
            return True

        except Exception as e:
            print(f"    ERROR: istio-system validation failed: {e}")
            return False

    def _validate_region_policies(self, namespace: str, region: str) -> bool:
        """Validate region security policies."""
        print(f"  Validating {namespace} policies...")

        try:
            # Check namespace exists with correct labels
            ns_resource = self.k8s.get_resource('namespace', namespace)
            if not ns_resource:
                print(f"    ERROR: Namespace {namespace} not found")
                return False

            labels = ns_resource.get('metadata', {}).get('labels', {})
            if labels.get('compliance.region') != region:
                print(f"    ERROR: Namespace {namespace} missing region label")
                return False

            if labels.get('istio-injection') != 'enabled':
                print(f"    ERROR: Namespace {namespace} missing istio-injection label")
                return False

            # Check PeerAuthentication
            peer_auth = self.k8s.get_resource('peerauthentications', 'default', namespace)
            if not peer_auth:
                print(f"    ERROR: PeerAuthentication not found in {namespace}")
                return False

            # Check AuthorizationPolicies
            authz_policies = self.k8s.get_resource('authorizationpolicies', namespace=namespace)
            if not authz_policies or len(authz_policies.get('items', [])) < 2:
                print(f"    ERROR: Authorization policies not found in {namespace}")
                return False

            # Check NetworkPolicy
            network_policy = self.k8s.get_resource('networkpolicies', 'baseline-zero-trust', namespace)
            if not network_policy:
                print(f"    ERROR: Network policy not found in {namespace}")
                return False

            print(f"    {namespace} policies validated")
            return True

        except Exception as e:
            print(f"    ERROR: {namespace} validation failed: {e}")
            return False

    def test_connectivity(self, regions: List[str]) -> bool:
        """Test network connectivity according to zero-trust policies."""
        print("Testing zero-trust network connectivity...")

        success = True
        for region in regions:
            namespace = f"region-{region}"
            if not self._test_region_connectivity(namespace):
                success = False

        return success

    def _test_region_connectivity(self, namespace: str) -> bool:
        """Test connectivity for a specific region."""
        print(f"  Testing connectivity in {namespace}...")

        try:
            # Create test pod
            test_pod_manifest = f"""
apiVersion: v1
kind: Pod
metadata:
  name: connectivity-test
  namespace: {namespace}
  labels:
    app: connectivity-test
spec:
  containers:
  - name: test
    image: busybox:1.36
    command: ['sleep', '300']
  restartPolicy: Never
"""

            if not self.k8s.apply_manifest(test_pod_manifest, namespace):
                print(f"    ERROR: Failed to create test pod in {namespace}")
                return False

            # Wait for pod to be ready
            time.sleep(10)

            # Test DNS resolution
            dns_result = self.k8s.execute_in_pod(
                'connectivity-test',
                ['nslookup', 'kubernetes.default.svc.cluster.local'],
                namespace
            )

            if not dns_result or 'Name:' not in dns_result:
                print(f"    ERROR: DNS resolution failed in {namespace}")
                success = False
            else:
                print(f"    DNS resolution working in {namespace}")
                success = True

            # Clean up test pod
            self.k8s.delete_manifest(test_pod_manifest, namespace)

            return success

        except Exception as e:
            print(f"    ERROR: Connectivity test failed in {namespace}: {e}")
            return False

    def cleanup_policies(self, regions: List[str]) -> bool:
        """Remove zero-trust policies from regions."""
        print("Cleaning up zero-trust policies...")

        success = True

        # Clean up region policies
        for region in regions:
            namespace = f"region-{region}"
            if not self._cleanup_region_policies(namespace):
                success = False

        # Clean up istio-system policies
        if not self._cleanup_istio_system_policies():
            success = False

        return success

    def _cleanup_region_policies(self, namespace: str) -> bool:
        """Clean up policies for a specific region."""
        print(f"  Cleaning up policies in {namespace}...")

        try:
            # Delete network policy
            network_policy_manifest = f"""
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: baseline-zero-trust
  namespace: {namespace}
"""
            self.k8s.delete_manifest(network_policy_manifest, namespace)

            # Delete authorization policies
            authz_manifest = f"""
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: allow-ingress-gateway
  namespace: {namespace}
---
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: deny-all
  namespace: {namespace}
"""
            self.k8s.delete_manifest(authz_manifest, namespace)

            # Delete peer authentication
            peer_auth_manifest = f"""
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: {namespace}
"""
            self.k8s.delete_manifest(peer_auth_manifest, namespace)

            print(f"  Policies cleaned up in {namespace}")
            return True

        except Exception as e:
            print(f"  ERROR: Failed to cleanup policies in {namespace}: {e}")
            return False

    def _cleanup_istio_system_policies(self) -> bool:
        """Clean up istio-system policies."""
        print("  Cleaning up istio-system policies...")

        try:
            istio_network_policy = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: istio-system-policy
  namespace: istio-system
"""
            self.k8s.delete_manifest(istio_network_policy, 'istio-system')

            print("  istio-system policies cleaned up")
            return True

        except Exception as e:
            print(f"  ERROR: Failed to cleanup istio-system policies: {e}")
            return False