"""Kubernetes utilities and API wrapper."""

import json
import os
import subprocess
import tempfile
import time
from typing import Dict, List, Optional, Any

import yaml
from kubernetes import client, config, dynamic, utils, watch
from kubernetes.client.exceptions import ApiException

# CRD mapping for dynamic client lookups
CRD_RESOURCE_MAP = {
    'virtualservice': ('networking.istio.io/v1beta1', 'VirtualService', True),
    'virtualservices': ('networking.istio.io/v1beta1', 'VirtualService', True),
    'gateway': ('networking.istio.io/v1beta1', 'Gateway', True),
    'gateways': ('networking.istio.io/v1beta1', 'Gateway', True),
    'peerauthentication': ('security.istio.io/v1beta1', 'PeerAuthentication', True),
    'peerauthentications': ('security.istio.io/v1beta1', 'PeerAuthentication', True),
    'authorizationpolicy': ('security.istio.io/v1beta1', 'AuthorizationPolicy', True),
    'authorizationpolicies': ('security.istio.io/v1beta1', 'AuthorizationPolicy', True),
    'clusterissuer': ('cert-manager.io/v1', 'ClusterIssuer', False),
    'clusterissuers': ('cert-manager.io/v1', 'ClusterIssuer', False),
    'certificate': ('cert-manager.io/v1', 'Certificate', True),
    'certificates': ('cert-manager.io/v1', 'Certificate', True),
    'certificaterequest': ('cert-manager.io/v1', 'CertificateRequest', True),
    'certificaterequests': ('cert-manager.io/v1', 'CertificateRequest', True),
    'tenant': ('minio.min.io/v2', 'Tenant', True),
    'tenants': ('minio.min.io/v2', 'Tenant', True),
}


class KubernetesClient:
    """Wrapper for Kubernetes API operations."""

    def __init__(self, namespace: str = 'default'):
        self.default_namespace = namespace
        self._init_client()

    def _init_client(self):
        """Initialize the Kubernetes API client with retries."""
        retries = 3
        for i in range(retries):
            try:
                config.load_kube_config()
                self.core_v1 = client.CoreV1Api()
                self.apps_v1 = client.AppsV1Api()
                self.custom_objects = client.CustomObjectsApi()
                self.api_client = client.ApiClient()
                self.storage_v1 = client.StorageV1Api()
                self.networking_v1 = client.NetworkingV1Api()
                self.autoscaling_v1 = client.AutoscalingV1Api()
                self.apiextensions_v1 = client.ApiextensionsV1Api()
                self.dynamic_client = dynamic.DynamicClient(self.api_client)
                # Test connection
                self.core_v1.get_api_resources()
                return
            except Exception as e:
                if i < retries - 1:
                    print(f"Failed to connect to Kubernetes API, retrying in 5 seconds... ({e})")
                    time.sleep(5)
                else:
                    print("Could not load kubeconfig or connect to Kubernetes API.")
                    self.core_v1 = None
                    self.apps_v1 = None
                    self.custom_objects = None
                    self.api_client = None
                    self.storage_v1 = None
                    self.networking_v1 = None
                    self.autoscaling_v1 = None
                    self.apiextensions_v1 = None
                    self.dynamic_client = None

    def apply_manifest(self, manifest: str, namespace: Optional[str] = None) -> bool:
        """Apply Kubernetes manifest from a string."""
        ns = namespace or self.default_namespace
        try:
            if not self.api_client:
                raise RuntimeError("Kubernetes API client unavailable")

            with open("/tmp/manifest.yaml", "w", encoding="utf-8") as f:
                f.write(manifest)
            utils.create_from_yaml(self.api_client, "/tmp/manifest.yaml", namespace=ns)
            return True
        except (ApiException, utils.FailToCreateError, AttributeError, RuntimeError) as e:
            print(f"Failed to apply manifest via API ({e}). Falling back to kubectl apply.")
            return self._kubectl_apply_from_stdin(manifest, ns)

    def apply_file(self, file_path: str, namespace: Optional[str] = None) -> bool:
        """Apply Kubernetes manifest from file."""
        ns = namespace or self.default_namespace
        try:
            if not self.api_client:
                raise RuntimeError("Kubernetes API client unavailable")

            utils.create_from_yaml(self.api_client, file_path, namespace=ns)
            return True
        except (ApiException, utils.FailToCreateError, AttributeError, RuntimeError) as e:
            print(f"Failed to apply file {file_path} via API ({e}). Falling back to kubectl apply.")
            return self._kubectl_apply_file(file_path, ns)

    def _kubectl_apply_from_stdin(self, manifest: str, namespace: str) -> bool:
        """Apply manifest using kubectl via stdin."""
        cmd = ['kubectl', 'apply']
        if namespace:
            cmd.extend(['-n', namespace])
        cmd.extend(['-f', '-'])

        try:
            subprocess.run(
                cmd,
                input=manifest,
                text=True,
                check=True,
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else e.stderr
            print(f"Failed to apply manifest via kubectl: {stderr}")
            return False

    def _kubectl_apply_file(self, file_path: str, namespace: str) -> bool:
        """Apply manifest file using kubectl."""
        cmd = ['kubectl', 'apply']
        if namespace:
            cmd.extend(['-n', namespace])
        cmd.extend(['-f', file_path])

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else e.stderr
            print(f"Failed to apply file via kubectl: {stderr}")
            return False

    def delete_manifest(self, manifest: str, namespace: Optional[str] = None) -> bool:
        """Delete resources from manifest."""
        ns = namespace or self.default_namespace
        temp_path = None
        try:
            if not self.api_client:
                raise RuntimeError("Kubernetes API client unavailable")

            fd, temp_path = tempfile.mkstemp(suffix='.yaml')
            with os.fdopen(fd, 'w', encoding='utf-8') as tmp:
                tmp.write(manifest)

            utils.delete_from_yaml(self.api_client, temp_path, namespace=ns)
            return True
        except (ApiException, FileNotFoundError, AttributeError, RuntimeError) as e:
            print(f"Failed to delete manifest via API ({e}). Falling back to kubectl delete.")
            cmd = ['kubectl', 'delete', '-f', '-', '-n', ns, '--ignore-not-found']
            try:
                subprocess.run(cmd, input=manifest, text=True, check=True, capture_output=True)
                return True
            except subprocess.CalledProcessError as sub_e:
                stderr = sub_e.stderr.decode('utf-8') if isinstance(sub_e.stderr, bytes) else sub_e.stderr
                print(f"Failed to delete manifest via kubectl: {stderr}")
                return False
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def get_resource(self, resource_type: str, name: Optional[str] = None,
                     namespace: Optional[str] = None, output: str = 'json') -> Optional[Dict]:
        """Get Kubernetes resource."""
        ns = namespace or self.default_namespace
        resource_type_lower = resource_type.lower()

        if not self.api_client:
            return self._kubectl_get(resource_type, name, namespace, output)

        try:
            return self._get_resource_via_api(resource_type_lower, name, namespace)
        except Exception:
            # Fallback to kubectl for unsupported resources
            return self._kubectl_get(resource_type, name, namespace, output)

    def _get_resource_via_api(self, resource_type: str, name: Optional[str], namespace: Optional[str]) -> Optional[Dict]:
        """Retrieve resource using Kubernetes Python APIs."""
        ns = namespace or self.default_namespace

        # Core resources
        if resource_type in {'pod', 'pods'}:
            if name:
                return self.core_v1.read_namespaced_pod(name, ns).to_dict()
            return self.core_v1.list_namespaced_pod(ns).to_dict()

        if resource_type in {'service', 'services'}:
            if name:
                return self.core_v1.read_namespaced_service(name, ns).to_dict()
            return self.core_v1.list_namespaced_service(ns).to_dict()

        if resource_type in {'endpoints', 'endpoint'}:
            if name:
                return self.core_v1.read_namespaced_endpoints(name, ns).to_dict()
            return self.core_v1.list_namespaced_endpoints(ns).to_dict()

        if resource_type in {'namespace', 'namespaces'}:
            if name:
                return self.core_v1.read_namespace(name).to_dict()
            return self.core_v1.list_namespace().to_dict()

        if resource_type in {'node', 'nodes'}:
            if name:
                return self.core_v1.read_node(name).to_dict()
            return self.core_v1.list_node().to_dict()

        if resource_type in {'secret', 'secrets'}:
            if name:
                return self.core_v1.read_namespaced_secret(name, ns).to_dict()
            return self.core_v1.list_namespaced_secret(ns).to_dict()

        if resource_type in {'configmap', 'configmaps'}:
            if name:
                return self.core_v1.read_namespaced_config_map(name, ns).to_dict()
            return self.core_v1.list_namespaced_config_map(ns).to_dict()

        if resource_type in {'deployment', 'deployments'}:
            if name:
                return self.apps_v1.read_namespaced_deployment(name, ns).to_dict()
            return self.apps_v1.list_namespaced_deployment(ns).to_dict()

        if resource_type in {'statefulset', 'statefulsets'}:
            if name:
                return self.apps_v1.read_namespaced_stateful_set(name, ns).to_dict()
            return self.apps_v1.list_namespaced_stateful_set(ns).to_dict()

        if resource_type in {'daemonset', 'daemonsets'}:
            if name:
                return self.apps_v1.read_namespaced_daemon_set(name, ns).to_dict()
            return self.apps_v1.list_namespaced_daemon_set(ns).to_dict()

        if resource_type in {'replicaset', 'replicasets'}:
            if name:
                return self.apps_v1.read_namespaced_replica_set(name, ns).to_dict()
            return self.apps_v1.list_namespaced_replica_set(ns).to_dict()

        if resource_type in {'storageclass', 'storageclasses'}:
            if name:
                return self.storage_v1.read_storage_class(name).to_dict()
            return self.storage_v1.list_storage_class().to_dict()

        if resource_type in {'networkpolicy', 'networkpolicies'}:
            if name:
                return self.networking_v1.read_namespaced_network_policy(name, ns).to_dict()
            return self.networking_v1.list_namespaced_network_policy(ns).to_dict()

        # CRDs via dynamic client
        if resource_type in CRD_RESOURCE_MAP and self.dynamic_client:
            api_version, kind, namespaced = CRD_RESOURCE_MAP[resource_type]
            resource = self.dynamic_client.resources.get(api_version=api_version, kind=kind)
            if name:
                if namespaced:
                    return resource.get(name=name, namespace=ns)
                return resource.get(name=name)
            if namespaced:
                return resource.get(namespace=ns)
            return resource.get()

        raise KeyError(f"Unsupported resource type: {resource_type}")

    def _kubectl_get(self, resource_type: str, name: Optional[str], namespace: Optional[str], output: str) -> Optional[Dict]:
        """Fallback to kubectl for resource retrieval."""
        cmd = ['kubectl', 'get', resource_type]
        if name:
            cmd.append(name)
        if namespace:
            cmd.extend(['-n', namespace])
        cmd.extend(['-o', output])
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if output == 'json':
                return json.loads(result.stdout)
            return {'output': result.stdout}
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None

    def wait_for_deployment(self, name: str, namespace: Optional[str] = None,
                            timeout: int = 300) -> bool:
        """Wait for deployment to be ready."""
        ns = namespace or self.default_namespace
        w = watch.Watch()
        try:
            for event in w.stream(self.apps_v1.list_namespaced_deployment,
                                  namespace=ns,
                                  field_selector=f"metadata.name={name}",
                                  timeout_seconds=timeout):
                deployment = event['object']
                if deployment.status.available_replicas == deployment.spec.replicas:
                    w.stop()
                    return True
            return False
        except ApiException as e:
            print(f"Error waiting for deployment {name}: {e}")
            return False

    def wait_for_pods(self, selector: str, namespace: Optional[str] = None,
                      timeout: int = 300) -> bool:
        """Wait for pods to be ready."""
        ns = namespace or self.default_namespace
        w = watch.Watch()
        try:
            for event in w.stream(self.core_v1.list_namespaced_pod,
                                  namespace=ns,
                                  label_selector=selector,
                                  timeout_seconds=timeout):
                pod = event['object']
                if pod.status.phase == 'Running':
                    all_containers_ready = all(
                        container.ready for container in pod.status.container_statuses
                    )
                    if all_containers_ready:
                        w.stop()
                        return True
            return False
        except ApiException as e:
            print(f"Error waiting for pods with selector {selector}: {e}")
            return False

    def create_namespace(self, namespace: str) -> bool:
        """Create namespace if it doesn't exist."""
        try:
            self.core_v1.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
            return True
        except ApiException as e:
            if e.status == 409:  # Already exists
                return True
            print(f"Failed to create namespace {namespace}: {e}")
            return False

    def ensure_namespace(self, namespace: str) -> bool:
        """Ensure a namespace exists (create if missing)."""
        return self.create_namespace(namespace)

    def label_namespace(self, namespace: str, labels: Dict[str, str]) -> bool:
        """Add labels to namespace."""
        body = {"metadata": {"labels": labels}}
        try:
            self.core_v1.patch_namespace(namespace, body)
            return True
        except ApiException as e:
            print(f"Failed to label namespace {namespace}: {e}")
            return False

    def get_pods(self, namespace: Optional[str] = None, selector: Optional[str] = None) -> List[Dict]:
        """Get pod information."""
        ns = namespace or self.default_namespace
        try:
            if selector:
                pods = self.core_v1.list_namespaced_pod(ns, label_selector=selector)
            else:
                pods = self.core_v1.list_namespaced_pod(ns)
            return [p.to_dict() for p in pods.items]
        except ApiException:
            return []

    def get_services(self, namespace: Optional[str] = None) -> List[Dict]:
        """Get service information."""
        ns = namespace or self.default_namespace
        try:
            services = self.core_v1.list_namespaced_service(ns)
            return [s.to_dict() for s in services.items]
        except ApiException:
            return []

    def port_forward(self, resource: str, ports: str, namespace: Optional[str] = None) -> subprocess.Popen:
        """Start port forwarding (returns process handle)."""
        # Port forwarding is a streaming operation, not easily done with the client.
        # Sticking with subprocess for this.
        ns = namespace or self.default_namespace
        cmd = ['kubectl', 'port-forward', resource, ports, '-n', ns]
        return subprocess.Popen(cmd)

    def execute_in_pod(self, pod_name: str, command: List[str],
                       namespace: Optional[str] = None, container: Optional[str] = None) -> Optional[str]:
        """Execute command in pod."""
        ns = namespace or self.default_namespace
        try:
            return self.core_v1.connect_get_namespaced_pod_exec(
                pod_name,
                ns,
                command=command,
                container=container,
                stderr=True, stdin=False, stdout=True, tty=False
            )
        except ApiException as e:
            print(f"Failed to execute command in pod {pod_name}: {e}")
            return None

    def get_logs(self, pod_name: str, namespace: Optional[str] = None,
                 container: Optional[str] = None, tail: Optional[int] = None) -> Optional[str]:
        """Get pod logs."""
        ns = namespace or self.default_namespace
        try:
            return self.core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=ns,
                container=container,
                tail_lines=tail
            )
        except ApiException as e:
            print(f"Failed to get logs for pod {pod_name}: {e}")
            return None


class HelmClient:
    """Wrapper for Helm operations."""

    def __init__(self):
        pass

    def add_repo(self, name: str, url: str) -> bool:
        """Add Helm repository."""
        try:
            subprocess.run(['helm', 'repo', 'add', name, url], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else e.stderr
            print(f"Failed to add repo {name}: {stderr}")
            return False

    def update_repos(self) -> bool:
        """Update Helm repositories."""
        try:
            subprocess.run(['helm', 'repo', 'update'], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else e.stderr
            print(f"Failed to update repos: {stderr}")
            return False

    def install(self, release_name: str, chart: str, namespace: str,
               values: Optional[Dict] = None, version: Optional[str] = None) -> bool:
        """Install Helm chart."""
        try:
            cmd = ['helm', 'install', release_name, chart, '-n', namespace, '--create-namespace']

            if version:
                cmd.extend(['--version', version])

            if values:
                values_file = f'/tmp/{release_name}-values.yaml'
                with open(values_file, 'w', encoding='utf-8') as f:
                    yaml.dump(values, f, default_flow_style=False)
                cmd.extend(['-f', values_file])

            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else e.stderr
            print(f"Failed to install {release_name}: {stderr}")
            return False

    def upgrade(self, release_name: str, chart: str, namespace: str,
               values: Optional[Dict] = None, version: Optional[str] = None) -> bool:
        """Upgrade Helm release."""
        try:
            cmd = ['helm', 'upgrade', release_name, chart, '-n', namespace]

            if version:
                cmd.extend(['--version', version])

            if values:
                values_file = f'/tmp/{release_name}-values.yaml'
                with open(values_file, 'w') as f:
                    yaml.dump(values, f)
                cmd.extend(['-f', values_file])

            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to upgrade {release_name}: {e}")
            return False

    def uninstall(self, release_name: str, namespace: str) -> bool:
        """Uninstall Helm release."""
        try:
            subprocess.run(['helm', 'uninstall', release_name, '-n', namespace], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else e.stderr
            print(f"Failed to uninstall {release_name}: {stderr}")
            return False

    def list_releases(self, namespace: Optional[str] = None) -> List[Dict]:
        """List Helm releases."""
        try:
            cmd = ['helm', 'list', '-o', 'json']
            if namespace:
                cmd.extend(['-n', namespace])
            else:
                cmd.append('--all-namespaces')

            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return []

    def get_values(self, release_name: str, namespace: str) -> Optional[Dict]:
        """Get Helm release values."""
        try:
            cmd = ['helm', 'get', 'values', release_name, '-n', namespace, '-o', 'json']
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None
