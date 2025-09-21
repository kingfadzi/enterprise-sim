"""Region lifecycle management."""

from typing import List
import yaml
from ..utils.k8s import KubernetesClient


class RegionManager:
    """Manages region namespaces and their security policies."""

    def __init__(self, k8s_client: KubernetesClient):
        self.k8s = k8s_client

    def setup_regions(self, regions: List[str]) -> bool:
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
        try:
            body = yaml.safe_load(peer_auth_manifest)
            self.k8s.custom_objects.create_namespaced_custom_object(
                group="security.istio.io",
                version="v1beta1",
                namespace=namespace,
                plural="peerauthentications",
                body=body,
            )
            print(f"    STRICT mTLS enforced in {namespace}")
            return True
        except Exception as e:
            # Check if it already exists
            if "AlreadyExists" in str(e):
                print(f"    PeerAuthentication already exists in {namespace}")
                return True
            print(f"ERROR: Failed to apply PeerAuthentication to {namespace}: {e}")
            return False


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
        try:
            for doc in yaml.safe_load_all(authz_policy_manifest):
                self.k8s.custom_objects.create_namespaced_custom_object(
                    group="security.istio.io",
                    version="v1beta1",
                    namespace=namespace,
                    plural="authorizationpolicies",
                    body=doc,
                )
            print(f"    Authorization policies applied to {namespace}")
            return True
        except Exception as e:
            if "AlreadyExists" in str(e):
                print(f"    AuthorizationPolicy already exists in {namespace}")
                return True
            print(f"ERROR: Failed to apply AuthorizationPolicy to {namespace}: {e}")
            return False

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
