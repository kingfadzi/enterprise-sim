"""k3d cluster lifecycle management."""

import subprocess
import time
import json
from typing import Optional, List, Dict
from .config import ClusterConfig


class ClusterManager:
    """Manages k3d cluster lifecycle operations."""

    def __init__(self, config: ClusterConfig):
        self.config = config

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
            '--port', f'{self.config.ingress_http_port}:80@loadbalancer',
            '--port', f'{self.config.ingress_https_port}:443@loadbalancer',
            '--port', f'{self.config.api_port}:6443@loadbalancer',
            '--k3s-arg', '--disable=traefik@server:*'
        ]


        # Add volume mounts if specified
        for mount in self.config.volume_mounts:
            cmd.extend(['--volume', mount])

        print(f"Running: {' '.join(cmd)}")

        try:
            print("Creating cluster infrastructure...")
            # Create cluster without --wait (faster, less prone to hanging)
            result = subprocess.run(cmd, check=True, capture_output=False, text=True, timeout=180)
            print("k3d cluster infrastructure created successfully")

            print("Waiting for cluster to be ready...")
            if self._wait_for_ready():
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
            result = subprocess.run([
                'k3d', 'kubeconfig', 'merge', self.config.name,
                '--kubeconfig-switch-context'
            ], check=True, capture_output=True, text=True, timeout=30)

            print(f"   Context switched to: k3d-{self.config.name}")

            # Verify the context switch worked
            try:
                verify_result = subprocess.run([
                    'kubectl', 'config', 'current-context'
                ], check=True, capture_output=True, text=True, timeout=10)

                current_context = verify_result.stdout.strip()
                expected_context = f"k3d-{self.config.name}"

                if current_context == expected_context:
                    print(f"   Verified context: {current_context}")
                    return True
                else:
                    print(f"   WARNING: Context mismatch: expected {expected_context}, got {current_context}")
                    return False

            except subprocess.CalledProcessError:
                print(f"   WARNING: Could not verify context switch")
                return True  # Still return True since merge succeeded

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

    def _wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for cluster to be ready."""
        print("Waiting for cluster nodes to be ready...")

        start_time = time.time()
        dots_count = 0

        while time.time() - start_time < timeout:
            try:
                result = subprocess.run([
                    'kubectl', 'get', 'nodes',
                    '--no-headers', '-o', 'custom-columns=STATUS:.status.conditions[?(@.type=="Ready")].status'
                ], check=True, capture_output=True, text=True, timeout=10)

                statuses = result.stdout.strip().split('\n')
                ready_nodes = [status.strip() for status in statuses if status.strip() == 'True']
                total_nodes = [status.strip() for status in statuses if status.strip()]

                if len(ready_nodes) == len(total_nodes) and len(total_nodes) > 0:
                    print(f"\nAll {len(total_nodes)} nodes are ready")
                    return True
                else:
                    # Show progress with dots
                    print(f"\r   Nodes ready: {len(ready_nodes)}/{len(total_nodes)} {'.' * (dots_count % 4)}", end='', flush=True)
                    dots_count += 1

            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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

        try:
            # Check nodes
            result = subprocess.run([
                'kubectl', 'get', 'nodes'
            ], check=True, capture_output=True, text=True)

            node_count = len([line for line in result.stdout.split('\n')
                            if line and not line.startswith('NAME')])

            expected_nodes = self.config.workers + 1  # workers + server
            if node_count != expected_nodes:
                print(f"Expected {expected_nodes} nodes, found {node_count}")
                return False

            # Check system pods
            result = subprocess.run([
                'kubectl', 'get', 'pods', '-n', 'kube-system',
                '--field-selector=status.phase!=Running'
            ], check=True, capture_output=True, text=True)

            if len(result.stdout.strip().split('\n')) > 1:  # More than header
                print("Some system pods are not running")
                return False

            print("Cluster validation passed")
            return True

        except subprocess.CalledProcessError as e:
            print(f"Cluster validation failed: {e.stderr}")
            return False