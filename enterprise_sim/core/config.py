"""Configuration management for enterprise simulation environment."""

import os
import yaml
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path


@dataclass
class ClusterConfig:
    """k3d cluster configuration."""
    name: str = "enterprise-sim"
    workers: int = 3
    registry_port: int = 5000
    api_port: int = 6443
    ingress_http_port: int = 8090  # Changed from 8080 to avoid Rancher conflicts
    ingress_https_port: int = 8453  # Changed from 8443 to avoid Rancher conflicts
    volume_mounts: List[str] = field(default_factory=list)


@dataclass
class ServiceConfig:
    """Individual service configuration."""
    enabled: bool = True
    version: str = "latest"
    config: Dict = field(default_factory=dict)


@dataclass
class EnterpriseConfig:
    """Complete enterprise simulation configuration."""
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    services: Dict[str, ServiceConfig] = field(default_factory=dict)
    environment: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize default service configurations."""
        if not self.services:
            self.services = {
                'istio': ServiceConfig(version='1.20.0'),
                'cert-manager': ServiceConfig(version='v1.13.0'),
                'storage': ServiceConfig(version='3.9.0'),
                'minio': ServiceConfig(version='7.1.1'),
                'sample-app': ServiceConfig(version='latest'),
            }


class ConfigManager:
    """Manages configuration loading and environment detection."""

    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or self._find_config_file()
        self.config = self._load_config()
        self._detect_environment()

    def _find_config_file(self) -> Optional[str]:
        """Find configuration file in standard locations."""
        possible_paths = [
            'config.yaml',
            'config.yml',
            'enterprise-sim.yaml',
            'enterprise-sim.yml',
            os.path.expanduser('~/.enterprise-sim.yaml'),
            '/etc/enterprise-sim/config.yaml'
        ]

        for path in possible_paths:
            if os.path.exists(path):
                return path
        return None

    def _load_config(self) -> EnterpriseConfig:
        """Load configuration from file or create default."""
        if self.config_file and os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                data = yaml.safe_load(f) or {}
            return self._dict_to_config(data)
        return EnterpriseConfig()

    def _dict_to_config(self, data: Dict) -> EnterpriseConfig:
        """Convert dictionary to EnterpriseConfig."""
        cluster_data = data.get('cluster', {})
        cluster = ClusterConfig(
            name=cluster_data.get('name', 'enterprise-sim'),
            workers=cluster_data.get('workers', 3),
            registry_port=cluster_data.get('registry_port', 5000),
            api_port=cluster_data.get('api_port', 6443),
            ingress_http_port=cluster_data.get('ingress_http_port', 8090),
            ingress_https_port=cluster_data.get('ingress_https_port', 8453),
            volume_mounts=cluster_data.get('volume_mounts', [])
        )

        services = {}
        for name, svc_data in data.get('services', {}).items():
            services[name] = ServiceConfig(
                enabled=svc_data.get('enabled', True),
                version=svc_data.get('version', 'latest'),
                config=svc_data.get('config', {})
            )

        return EnterpriseConfig(
            cluster=cluster,
            services=services,
            environment=data.get('environment', {})
        )

    def _detect_environment(self):
        """Detect and validate environment dependencies."""
        required_tools = ['k3d', 'kubectl', 'docker', 'helm']
        missing_tools = []

        for tool in required_tools:
            if not self._command_exists(tool):
                missing_tools.append(tool)

        if missing_tools:
            raise EnvironmentError(
                f"Missing required tools: {', '.join(missing_tools)}"
            )

        # Set environment variables
        os.environ.setdefault('KUBECONFIG', os.path.expanduser('~/.kube/config'))

        # Apply configuration environment overrides
        for key, value in self.config.environment.items():
            os.environ[key] = value

    def _command_exists(self, command: str) -> bool:
        """Check if command exists in PATH."""
        from shutil import which
        return which(command) is not None

    def get_cluster_config(self) -> ClusterConfig:
        """Get cluster configuration."""
        return self.config.cluster

    def get_service_config(self, service_name: str) -> Optional[ServiceConfig]:
        """Get configuration for specific service."""
        return self.config.services.get(service_name)

    def is_service_enabled(self, service_name: str) -> bool:
        """Check if service is enabled."""
        service = self.get_service_config(service_name)
        return service.enabled if service else False

    def save_config(self, output_file: Optional[str] = None):
        """Save current configuration to file."""
        output_file = output_file or self.config_file or 'enterprise-sim.yaml'

        data = {
            'cluster': {
                'name': self.config.cluster.name,
                'workers': self.config.cluster.workers,
                'registry_port': self.config.cluster.registry_port,
                'api_port': self.config.cluster.api_port,
                'ingress_http_port': self.config.cluster.ingress_http_port,
                'ingress_https_port': self.config.cluster.ingress_https_port,
                'volume_mounts': self.config.cluster.volume_mounts
            },
            'services': {
                name: {
                    'enabled': svc.enabled,
                    'version': svc.version,
                    'config': svc.config
                }
                for name, svc in self.config.services.items()
            },
            'environment': self.config.environment
        }

        with open(output_file, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, indent=2)