"""Base service interface for enterprise simulation services."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from ..core.config import ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


class ServiceStatus(Enum):
    """Service status enumeration."""
    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"
    UPGRADING = "upgrading"
    UNINSTALLING = "uninstalling"


class ServiceHealth(Enum):
    """Service health enumeration."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class BaseService(ABC):
    """Abstract base class for all enterprise simulation services."""

    def __init__(
        self,
        config: ServiceConfig,
        k8s_client: KubernetesClient,
        helm_client: HelmClient,
        global_context: Optional[Dict[str, Any]] = None,
    ):
        self.config = config
        self.k8s = k8s_client
        self.helm = helm_client
        self.global_context: Dict[str, Any] = global_context or {}
        self._status = ServiceStatus.NOT_INSTALLED

    @property
    @abstractmethod
    def name(self) -> str:
        """Service name."""
        pass

    @property
    @abstractmethod
    def namespace(self) -> str:
        """Default namespace for the service."""
        pass

    @property
    @abstractmethod
    def dependencies(self) -> Set[str]:
        """Set of service names this service depends on."""
        pass

    @property
    @abstractmethod
    def helm_chart(self) -> Optional[Dict[str, str]]:
        """Helm chart information: {'repo': 'repo_name', 'chart': 'chart_name'}."""
        pass

    @property
    def status(self) -> ServiceStatus:
        """Current service status."""
        return self._status

    @abstractmethod
    def get_helm_values(self) -> Dict:
        """Get Helm values for this service."""
        pass

    @abstractmethod
    def validate_prerequisites(self) -> bool:
        """Validate prerequisites before installation."""
        pass

    @abstractmethod
    def post_install_tasks(self) -> bool:
        """Execute post-installation tasks."""
        pass

    @abstractmethod
    def get_health(self) -> ServiceHealth:
        """Get current service health status."""
        pass

    @abstractmethod
    def get_endpoints(self, domain: str) -> List[Dict[str, str]]:
        """Get service endpoints for external access."""
        pass

    def install(self) -> bool:
        """Install the service."""
        if not self.config.enabled:
            print(f"Service {self.name} is disabled, skipping installation")
            return True

        print(f"Installing {self.name}...")
        self._status = ServiceStatus.INSTALLING

        try:
            # Validate prerequisites
            if not self.validate_prerequisites():
                print(f"Prerequisites validation failed for {self.name}")
                self._status = ServiceStatus.FAILED
                return False

            # Create namespace
            if not self.k8s.create_namespace(self.namespace):
                print(f"Failed to create namespace {self.namespace}")
                self._status = ServiceStatus.FAILED
                return False

            # Install via Helm if chart is specified
            if self.helm_chart:
                success = self._install_helm_chart()
            else:
                success = self._install_custom()

            if not success:
                self._status = ServiceStatus.FAILED
                return False

            # Execute post-install tasks
            if not self.post_install_tasks():
                print(f"Post-install tasks failed for {self.name}")
                self._status = ServiceStatus.FAILED
                return False

            print(f"✅ {self.name} installed successfully")
            self._status = ServiceStatus.INSTALLED
            return True

        except Exception as e:
            print(f"❌ Failed to install {self.name}: {e}")
            self._status = ServiceStatus.FAILED
            return False

    def _install_helm_chart(self) -> bool:
        """Install service via Helm chart."""
        chart_info = self.helm_chart
        if not chart_info:
            return False

        # Add repository if specified
        if 'repo_url' in chart_info:
            if not self.helm.add_repo(chart_info['repo'], chart_info['repo_url']):
                return False

        # Update repositories
        if not self.helm.update_repos():
            return False

        # Install chart
        chart_name = f"{chart_info['repo']}/{chart_info['chart']}"
        values = self.get_helm_values()

        return self.helm.install(
            release_name=self.name,
            chart=chart_name,
            namespace=self.namespace,
            values=values,
            version=self.config.version if self.config.version != 'latest' else None
        )

    def _install_custom(self) -> bool:
        """Install service via custom implementation."""
        # Override in subclasses for custom installation logic
        return True

    def uninstall(self) -> bool:
        """Uninstall the service."""
        print(f"Uninstalling {self.name}...")
        self._status = ServiceStatus.UNINSTALLING

        try:
            if self.helm_chart:
                # Uninstall Helm release
                success = self.helm.uninstall(self.name, self.namespace)
            else:
                success = self._uninstall_custom()

            if success:
                print(f"✅ {self.name} uninstalled successfully")
                self._status = ServiceStatus.NOT_INSTALLED
            else:
                self._status = ServiceStatus.FAILED

            return success

        except Exception as e:
            print(f"❌ Failed to uninstall {self.name}: {e}")
            self._status = ServiceStatus.FAILED
            return False

    def _uninstall_custom(self) -> bool:
        """Uninstall service via custom implementation."""
        # Override in subclasses for custom uninstallation logic
        return True

    def upgrade(self) -> bool:
        """Upgrade the service."""
        if not self.config.enabled:
            print(f"Service {self.name} is disabled, skipping upgrade")
            return True

        print(f"Upgrading {self.name}...")
        self._status = ServiceStatus.UPGRADING

        try:
            if self.helm_chart:
                chart_info = self.helm_chart
                chart_name = f"{chart_info['repo']}/{chart_info['chart']}"
                values = self.get_helm_values()

                success = self.helm.upgrade(
                    release_name=self.name,
                    chart=chart_name,
                    namespace=self.namespace,
                    values=values,
                    version=self.config.version if self.config.version != 'latest' else None
                )
            else:
                success = self._upgrade_custom()

            if success:
                print(f"✅ {self.name} upgraded successfully")
                self._status = ServiceStatus.INSTALLED
            else:
                self._status = ServiceStatus.FAILED

            return success

        except Exception as e:
            print(f"❌ Failed to upgrade {self.name}: {e}")
            self._status = ServiceStatus.FAILED
            return False

    def _upgrade_custom(self) -> bool:
        """Upgrade service via custom implementation."""
        # Override in subclasses for custom upgrade logic
        return True

    def is_installed(self) -> bool:
        """Check if service is currently installed."""
        try:
            if self.helm_chart:
                releases = self.helm.list_releases(self.namespace)
                return any(release['name'] == self.name for release in releases)
            else:
                return self._is_installed_custom()
        except:
            return False

    def _is_installed_custom(self) -> bool:
        """Custom implementation to check if service is installed."""
        # Override in subclasses
        return False

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for service to be ready."""
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.get_health() == ServiceHealth.HEALTHY:
                return True
            time.sleep(10)

        return False

    def get_info(self, domain: str) -> Dict:
        """Get comprehensive service information."""
        return {
            'name': self.name,
            'namespace': self.namespace,
            'status': self.status.value,
            'health': self.get_health().value,
            'enabled': self.config.enabled,
            'version': self.config.version,
            'dependencies': list(self.dependencies),
            'endpoints': self.get_endpoints(domain),
            'installed': self.is_installed()
        }
