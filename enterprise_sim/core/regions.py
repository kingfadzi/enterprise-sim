"""Region lifecycle management."""

import time
from typing import List
from ..utils.k8s import KubernetesClient
from ..utils.manifests import load_manifest_documents, load_single_manifest, render_manifest


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

        if not self._wait_for_istio_crds():
            print("ERROR: Required Istio CRDs are not available. Aborting region setup.")
            return False

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
        manifest_text = render_manifest(
            "manifests/regions/namespace.yaml",
            namespace=namespace,
            region=region,
        )

        if not self.k8s.apply_manifest(manifest_text):
            print(f"ERROR: Failed to create namespace {namespace}")
            return False

        print(f"    Namespace {namespace} configured with zero-trust labels")
        return True

    def _apply_peer_authentication(self, namespace: str) -> bool:
        """Apply STRICT mTLS PeerAuthentication policy."""
        print(f"    Applying STRICT mTLS policy to {namespace}")

        try:
            body = load_single_manifest(
                "manifests/regions/peer-auth.yaml",
                namespace=namespace,
            )
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

    def _wait_for_istio_crds(self, timeout: int = 120) -> bool:
        """Wait for Istio CRDs to be registered before applying policies."""
        required_crds = [
            'peerauthentications.security.istio.io',
            'authorizationpolicies.security.istio.io',
            'gateways.networking.istio.io',
        ]

        start = time.time()
        while time.time() - start < timeout:
            missing = [
                crd for crd in required_crds
                if not self.k8s.get_resource('customresourcedefinitions', crd)
            ]
            if not missing:
                return True

            wait_remaining = timeout - int(time.time() - start)
            print(
                "  Waiting for Istio CRDs to be ready (missing: {} | {}s remaining)".format(
                    ', '.join(missing),
                    wait_remaining,
                )
            )
            time.sleep(5)

        print("ERROR: Timed out waiting for Istio CRDs: {}".format(', '.join(required_crds)))
        return False


    def _apply_authorization_policy(self, namespace: str) -> bool:
        """Apply minimal AuthorizationPolicy allowing ingress."""
        print(f"    Applying authorization policy to {namespace}")

        try:
            documents = load_manifest_documents(
                "manifests/regions/authz-allow-ingress.yaml",
                namespace=namespace,
            ) + load_manifest_documents(
                "manifests/regions/authz-deny-all.yaml",
                namespace=namespace,
            )

            for doc in documents:
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

        manifest_text = render_manifest(
            "manifests/regions/network-policy.yaml",
            namespace=namespace,
        )

        if not self.k8s.apply_manifest(manifest_text, namespace):
            print(f"ERROR: Failed to apply NetworkPolicy to {namespace}")
            return False

        print(f"    Zero-trust network policy applied to {namespace}")
        return True
