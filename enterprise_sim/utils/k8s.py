"""Kubernetes utilities and API wrapper."""

import subprocess
import json
import yaml
import time
from typing import Dict, List, Optional, Any
from pathlib import Path


class KubernetesClient:
    """Wrapper for kubectl operations."""

    def __init__(self, namespace: str = 'default'):
        self.default_namespace = namespace

    def apply_manifest(self, manifest: str, namespace: Optional[str] = None) -> bool:
        """Apply Kubernetes manifest."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'apply', '-f', '-', '-n', ns]
            result = subprocess.run(
                cmd, input=manifest, text=True,
                check=True, capture_output=True
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to apply manifest: {e.stderr}")
            if e.stderr and "error parsing" in e.stderr:
                print("Manifest content for debugging:")
                print("---")
                for i, line in enumerate(manifest.split('\n'), 1):
                    print(f"{i:2d}: {repr(line)}")
                print("---")
            return False

    def apply_file(self, file_path: str, namespace: Optional[str] = None) -> bool:
        """Apply Kubernetes manifest from file."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'apply', '-f', file_path, '-n', ns]
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to apply file {file_path}: {e.stderr}")
            return False

    def delete_manifest(self, manifest: str, namespace: Optional[str] = None) -> bool:
        """Delete resources from manifest."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'delete', '-f', '-', '-n', ns, '--ignore-not-found']
            subprocess.run(
                cmd, input=manifest, text=True,
                check=True, capture_output=True
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to delete manifest: {e.stderr}")
            return False

    def get_resource(self, resource_type: str, name: Optional[str] = None,
                    namespace: Optional[str] = None, output: str = 'json') -> Optional[Dict]:
        """Get Kubernetes resource."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'get', resource_type]
            if name:
                cmd.append(name)
            cmd.extend(['-n', ns, '-o', output])

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

        try:
            cmd = [
                'kubectl', 'wait', f'deployment/{name}',
                '--for=condition=available',
                f'--timeout={timeout}s',
                '-n', ns
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def wait_for_pods(self, selector: str, namespace: Optional[str] = None,
                     timeout: int = 300) -> bool:
        """Wait for pods to be ready."""
        ns = namespace or self.default_namespace

        try:
            cmd = [
                'kubectl', 'wait', 'pods',
                '--for=condition=ready',
                f'--selector={selector}',
                f'--timeout={timeout}s',
                '-n', ns
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def create_namespace(self, namespace: str) -> bool:
        """Create namespace if it doesn't exist."""
        try:
            cmd = ['kubectl', 'create', 'namespace', namespace]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else e.stderr
            if 'already exists' in stderr:
                return True
            print(f"Failed to create namespace {namespace}: {stderr}")
            return False

    def label_namespace(self, namespace: str, labels: Dict[str, str]) -> bool:
        """Add labels to namespace."""
        try:
            label_str = ','.join([f'{k}={v}' for k, v in labels.items()])
            cmd = ['kubectl', 'label', 'namespace', namespace, label_str, '--overwrite']
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to label namespace {namespace}: {e.stderr}")
            return False

    def get_pods(self, namespace: Optional[str] = None, selector: Optional[str] = None) -> List[Dict]:
        """Get pod information."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'get', 'pods', '-n', ns, '-o', 'json']
            if selector:
                cmd.extend(['--selector', selector])

            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            data = json.loads(result.stdout)
            return data.get('items', [])
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return []

    def get_services(self, namespace: Optional[str] = None) -> List[Dict]:
        """Get service information."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'get', 'services', '-n', ns, '-o', 'json']
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            data = json.loads(result.stdout)
            return data.get('items', [])
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return []

    def port_forward(self, resource: str, ports: str, namespace: Optional[str] = None) -> subprocess.Popen:
        """Start port forwarding (returns process handle)."""
        ns = namespace or self.default_namespace

        cmd = ['kubectl', 'port-forward', resource, ports, '-n', ns]
        return subprocess.Popen(cmd)

    def execute_in_pod(self, pod_name: str, command: List[str],
                      namespace: Optional[str] = None, container: Optional[str] = None) -> Optional[str]:
        """Execute command in pod."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'exec', pod_name, '-n', ns]
            if container:
                cmd.extend(['-c', container])
            cmd.append('--')
            cmd.extend(command)

            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            print(f"Failed to execute command in pod {pod_name}: {e.stderr}")
            return None

    def get_logs(self, pod_name: str, namespace: Optional[str] = None,
                container: Optional[str] = None, tail: Optional[int] = None) -> Optional[str]:
        """Get pod logs."""
        ns = namespace or self.default_namespace

        try:
            cmd = ['kubectl', 'logs', pod_name, '-n', ns]
            if container:
                cmd.extend(['-c', container])
            if tail:
                cmd.extend(['--tail', str(tail)])

            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            print(f"Failed to get logs for pod {pod_name}: {e.stderr}")
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
            print(f"Failed to upgrade {release_name}: {e.stderr}")
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