"""Zero-trust network policies and security defaults."""

from typing import List
from ..utils.k8s import KubernetesClient


class PolicyManager:
    """Manages zero-trust network policies and security configurations."""

    def __init__(self, k8s_client: KubernetesClient):
        self.k8s = k8s_client

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
