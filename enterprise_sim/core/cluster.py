"""k3d cluster lifecycle management."""

import json
import os
import subprocess
import time
from typing import Optional, List, Dict

from kubernetes import config as k8s_config

from .config import ClusterConfig
from ..utils.k8s import KubernetesClient


class ClusterManager:
    """Manages k3d cluster lifecycle operations."""

    def __init__(self, config: ClusterConfig):
        self.config = config
        self.k8s_client = None

    def _get_k8s_client(self) -> KubernetesClient:
        """Lazily initialize and return the Kubernetes client."""
        if not self.k8s_client:
            self.k8s_client = KubernetesClient()
        return self.k8s_client

    def create(self, force: bool = False) -> bool:
        """Create k3d cluster with enterprise configuration."""
        if self.exists():
            if force:
                print(f"Cluster {self.config.name} exists. Deleting...")
                self.delete()
            else:
                print(f"Cluster {self.config.name} already exists")
                return True

        print(f"Creating k3d cluster: {self.config.name}")
        print(f"   Workers: {self.config.workers}")
        print(f"   Registry port: {self.config.registry_port}")
        print(f"   HTTP port: {self.config.ingress_http_port}")
        print(f"   HTTPS port: {self.config.ingress_https_port}")

        # Build k3d create command (removed --wait to prevent hanging)
        cmd = [
            'k3d', 'cluster', 'create', self.config.name,
            '--agents', str(self.config.workers),
            '--registry-create', f'{self.config.name}-registry:{self.config.registry_port}',
            '--api-port', f'127.0.0.1:{self.config.api_port}',
            '--port', f'{self.config.ingress_http_port}:80@loadbalancer',
            '--port', f'{self.config.ingress_https_port}:443@loadbalancer',
            '--k3s-arg', '--disable=traefik@server:*'
        ]

        # Add volume mounts if specified
        for mount in self.config.volume_mounts:
            host_path, container_path = mount.split(':', 1)
            expanded_host_path = os.path.expanduser(host_path)

            if not os.path.isabs(expanded_host_path):
                expanded_host_path = os.path.abspath(expanded_host_path)

            cmd.extend(['--volume', f'{expanded_host_path}:{container_path}'])

        print(f"Running: {' '.join(cmd)}")

        try:
            print("Creating cluster infrastructure...")
            # Create cluster without --wait (faster, less prone to hanging)
            subprocess.run(cmd, check=True, capture_output=False, text=True, timeout=180)
            print("k3d cluster infrastructure created successfully")

            # IMPORTANT: Update kubeconfig BEFORE initializing the client
            self.get_kubeconfig()
            self._fix_kubeconfig()
            self.k8s_client = None  # Force re-initialization

            print("Waiting for cluster to be ready...")
            if self._wait_for_api_server() and self._wait_for_ready():
                print("Cluster is ready and operational")
                return True
            else:
                print("WARNING: Cluster created but some nodes may still be starting")
                print("         You can check status with: kubectl get nodes")
                return True  # Still return True since cluster was created

        except subprocess.TimeoutExpired:
            print("ERROR: Cluster creation timed out (3 minutes)")
            print("       You may need to delete and retry: k3d cluster delete " + self.config.name)
            return False
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to create cluster")
            print(f"       Error details: {e}")
            return False

    def delete(self) -> bool:
        """Delete k3d cluster."""
        if not self.exists():
            print(f"Cluster {self.config.name} does not exist")
            return True

        print(f"Deleting k3d cluster: {self.config.name}")

        try:
            subprocess.run([
                'k3d', 'cluster', 'delete', self.config.name
            ], check=True, capture_output=True)

            print("Cluster deleted successfully")
            return True

        except subprocess.CalledProcessError as e:
            print(f"Failed to delete cluster: {e.stderr}")
            return False

    def exists(self) -> bool:
        """Check if cluster exists."""
        try:
            result = subprocess.run([
                'k3d', 'cluster', 'list', '--output', 'json'
            ], check=True, capture_output=True, text=True)

            clusters = json.loads(result.stdout)
            return any(cluster['name'] == self.config.name for cluster in clusters)

        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return False

    def start(self) -> bool:
        """Start existing cluster."""
        if not self.exists():
            print(f"Cluster {self.config.name} does not exist")
            return False

        try:
            subprocess.run([
                'k3d', 'cluster', 'start', self.config.name
            ], check=True, capture_output=True)

            print(f"Cluster {self.config.name} started")
            self._wait_for_ready()
            return True

        except subprocess.CalledProcessError as e:
            print(f"Failed to start cluster: {e.stderr}")
            return False

    def stop(self) -> bool:
        """Stop running cluster."""
        if not self.exists():
            print(f"Cluster {self.config.name} does not exist")
            return True

        try:
            subprocess.run([
                'k3d', 'cluster', 'stop', self.config.name
            ], check=True, capture_output=True)

            print(f"Cluster {self.config.name} stopped")
            return True

        except subprocess.CalledProcessError as e:
            print(f"Failed to stop cluster: {e.stderr}")
            return False

    def get_status(self) -> Optional[Dict]:
        """Get cluster status information."""
        try:
            result = subprocess.run([
                'k3d', 'cluster', 'list', '--output', 'json'
            ], check=True, capture_output=True, text=True)

            clusters = json.loads(result.stdout)
            for cluster in clusters:
                if cluster['name'] == self.config.name:
                    return cluster
            return None

        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None

    def get_kubeconfig(self) -> bool:
        """Update kubeconfig for cluster access."""
        try:
            print(f"   Merging kubeconfig for cluster: {self.config.name}")
            subprocess.run([
                'k3d', 'kubeconfig', 'merge', self.config.name,
                '--kubeconfig-switch-context'
            ], check=True, capture_output=True, text=True, timeout=30)

            print(f"   Context switched to: k3d-{self.config.name}")

            # Verify the context switch worked using kubernetes config helpers
            try:
                _, current_context = k8s_config.list_kube_config_contexts()
            except Exception:
                print("   WARNING: Could not verify context switch")
                return True  # Merge succeeded even if verification failed

            expected_context = f"k3d-{self.config.name}"

            if current_context and current_context.get('name') == expected_context:
                print(f"   Verified context: {expected_context}")
                return True

            print(f"   WARNING: Context mismatch: expected {expected_context}, got {current_context.get('name') if current_context else 'none'}")
            return False

        except subprocess.TimeoutExpired:
            print(f"   ERROR: Kubeconfig merge timed out")
            return False
        except subprocess.CalledProcessError as e:
            print(f"   ERROR: Failed to update kubeconfig: {e.stderr}")
            return False

    def get_registry_info(self) -> Optional[Dict]:
        """Get cluster registry information."""
        registry_name = f'{self.config.name}-registry'

        try:
            result = subprocess.run([
                'k3d', 'registry', 'list', '--output', 'json'
            ], check=True, capture_output=True, text=True)

            registries = json.loads(result.stdout)
            for registry in registries:
                if registry['name'] == registry_name:
                    return {
                        'name': registry['name'],
                        'host': f'localhost:{self.config.registry_port}',
                        'internal_host': f'{registry_name}:5000'
                    }
            return None

        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None

    def _fix_kubeconfig(self):
        """Correct the server address in the kubeconfig file if it points to 0.0.0.0."""
        import yaml
        from pathlib import Path

        try:
            kubeconfig_path = Path.home() / ".kube" / "config"
            if not kubeconfig_path.exists():
                print("   WARNING: Kubeconfig file not found.")
                return

            with open(kubeconfig_path, 'r') as f:
                kubeconfig = yaml.safe_load(f)

            cluster_name = f"k3d-{self.config.name}"
            cluster_found = False
            for cluster in kubeconfig.get("clusters", []):
                if cluster.get("name") == cluster_name:
                    server = cluster.get("cluster", {}).get("server", "")
                    if "0.0.0.0" in server:
                        new_server = server.replace("0.0.0.0", "127.0.0.1")
                        cluster["cluster"]["server"] = new_server
                        cluster_found = True
                        break
            
            if cluster_found:
                with open(kubeconfig_path, 'w') as f:
                    yaml.dump(kubeconfig, f)
                print(f"   ✅ Corrected kubeconfig server address for {cluster_name}")

        except Exception as e:
            print(f"   WARNING: Failed to fix kubeconfig: {e}")

    def _wait_for_api_server(self, timeout: int = 60) -> bool:
        """Wait for the Kubernetes API server to be ready."""
        print("Waiting for API server to be ready...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # A lightweight command to check if the API server is responsive.
                self._get_k8s_client().core_v1.get_api_resources()
                print("\nAPI server is ready.")
                return True
            except Exception:
                print(".", end='', flush=True)
                time.sleep(2)
        print("\nTimeout waiting for API server.")
        return False

    def _wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for cluster to be ready."""
        print("Waiting for cluster nodes to be ready...")

        start_time = time.time()
        dots_count = 0

        while time.time() - start_time < timeout:
            nodes = self._get_k8s_client().get_resource('nodes', output='json')
            if nodes and 'items' in nodes:
                total_nodes = len(nodes['items'])
                ready_nodes = 0
                for node in nodes['items']:
                    for condition in node['status']['conditions']:
                        if condition['type'] == 'Ready' and condition['status'] == 'True':
                            ready_nodes += 1
                
                if ready_nodes == total_nodes and total_nodes > 0:
                    print(f"\nAll {total_nodes} nodes are ready")
                    return True
                else:
                    print(f"\r   Nodes ready: {ready_nodes}/{total_nodes} {'.' * (dots_count % 4)}", end='', flush=True)
                    dots_count += 1
            else:
                print(f"\r   Checking cluster readiness {'.' * (dots_count % 4)}", end='', flush=True)
                dots_count += 1

            time.sleep(5)

        print(f"\nTimeout waiting for cluster to be ready (waited {timeout}s)")
        return False

    def validate_cluster(self) -> bool:
        """Validate cluster is properly configured."""
        if not self.exists():
            print("Cluster does not exist")
            return False

        # Check nodes
        nodes = self._get_k8s_client().get_resource('nodes', output='json')
        if not nodes or 'items' not in nodes:
            print("Could not get cluster nodes.")
            return False

        node_count = len(nodes['items'])
        expected_nodes = self.config.workers + 1  # workers + server
        if node_count != expected_nodes:
            print(f"Expected {expected_nodes} nodes, found {node_count}")
            return False

        # Check system pods
        pods = self._get_k8s_client().get_pods(namespace='kube-system')
        non_running_pods = [
            pod['metadata']['name'] for pod in pods
            if pod['status']['phase'] != 'Running'
        ]

        if non_running_pods:
            print(f"Some system pods are not running: {non_running_pods}")
            return False

        print("Cluster validation passed")
        return True
