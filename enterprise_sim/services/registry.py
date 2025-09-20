"""Service registry and dependency management."""

from typing import Dict, List, Set, Type, Optional
import time
from .base import BaseService, ServiceStatus


class DependencyError(Exception):
    """Raised when dependency resolution fails."""
    pass


class ServiceRegistry:
    """Manages service registration and dependency resolution."""

    def __init__(self):
        self._services: Dict[str, Type[BaseService]] = {}
        self._instances: Dict[str, BaseService] = {}

    def register(self, service_class: Type[BaseService]):
        """Register a service class."""
        # Get service name from a temporary instance
        temp_instance = service_class.__new__(service_class)
        service_name = temp_instance.name

        self._services[service_name] = service_class
        print(f"Registered service: {service_name}")

    def create_instance(self, service_name: str, config, k8s_client, helm_client) -> BaseService:
        """Create service instance."""
        if service_name not in self._services:
            raise ValueError(f"Service {service_name} not registered")

        service_class = self._services[service_name]
        instance = service_class(config, k8s_client, helm_client)
        self._instances[service_name] = instance
        return instance

    def get_service(self, service_name: str) -> Optional[BaseService]:
        """Get service instance."""
        return self._instances.get(service_name)

    def get_all_services(self) -> Dict[str, BaseService]:
        """Get all service instances."""
        return self._instances.copy()

    def resolve_dependencies(self, target_services: List[str]) -> List[str]:
        """Resolve service dependencies and return installation order."""
        if not target_services:
            return []

        # Build dependency graph
        dependency_graph = {}
        all_services = set(target_services)

        # Collect all dependencies
        to_process = list(target_services)
        while to_process:
            service_name = to_process.pop(0)
            if service_name in dependency_graph:
                continue

            if service_name not in self._instances:
                raise DependencyError(f"Service {service_name} not found in instances")

            service = self._instances[service_name]
            dependencies = service.dependencies
            dependency_graph[service_name] = dependencies

            # Add dependencies to processing queue
            for dep in dependencies:
                if dep not in all_services:
                    all_services.add(dep)
                    to_process.append(dep)

        # Topological sort for installation order
        return self._topological_sort(dependency_graph)

    def _topological_sort(self, graph: Dict[str, Set[str]]) -> List[str]:
        """Perform topological sort on dependency graph."""
        # Kahn's algorithm
        in_degree = {node: 0 for node in graph}

        # Calculate in-degrees
        for node in graph:
            for neighbor in graph[node]:
                if neighbor in in_degree:
                    in_degree[neighbor] += 1

        # Find nodes with no dependencies
        queue = [node for node, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            # Remove edges and update in-degrees
            for neighbor in graph.get(node, set()):
                if neighbor in in_degree:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

        # Check for cycles
        if len(result) != len(graph):
            cycle_nodes = [node for node, degree in in_degree.items() if degree > 0]
            raise DependencyError(f"Circular dependency detected involving: {cycle_nodes}")

        return result

    def install_services(self, service_names: List[str], timeout: int = 1800) -> bool:
        """Install services in dependency order."""
        try:
            # Resolve dependencies
            install_order = self.resolve_dependencies(service_names)
            print(f"Installation order: {' -> '.join(install_order)}")

            # Install services
            for service_name in install_order:
                service = self._instances.get(service_name)
                if not service:
                    print(f"ERROR: Service {service_name} not found")
                    return False

                if not service.config.enabled:
                    print(f"SKIPPING: Service {service_name} is disabled")
                    continue

                # Check if service is already installed
                if service.is_installed():
                    print(f"SKIPPING: {service_name} is already installed")
                    continue

                print(f"\nInstalling {service_name}...")
                start_time = time.time()

                if not service.install():
                    print(f"ERROR: Failed to install {service_name}")
                    return False

                # Wait for service to be ready
                print(f"Waiting for {service_name} to be ready...")
                if not service.wait_for_ready(timeout=300):
                    print(f"WARNING: {service_name} installation timed out, but continuing...")

                elapsed = time.time() - start_time
                print(f"{service_name} ready in {elapsed:.1f}s")

            print(f"\nAll services installed successfully!")
            return True

        except DependencyError as e:
            print(f"ERROR: Dependency error: {e}")
            return False
        except Exception as e:
            print(f"ERROR: Installation failed: {e}")
            return False

    def uninstall_services(self, service_names: List[str]) -> bool:
        """Uninstall services in reverse dependency order."""
        try:
            # Resolve dependencies and reverse for uninstallation
            uninstall_order = list(reversed(self.resolve_dependencies(service_names)))
            print(f"Uninstallation order: {' -> '.join(uninstall_order)}")

            # Uninstall services
            success = True
            for service_name in uninstall_order:
                service = self._instances.get(service_name)
                if not service:
                    print(f"WARNING: Service {service_name} not found, skipping")
                    continue

                print(f"Uninstalling {service_name}...")
                if not service.uninstall():
                    print(f"ERROR: Failed to uninstall {service_name}")
                    success = False
                else:
                    print(f"{service_name} uninstalled")

            return success

        except DependencyError as e:
            print(f"ERROR: Dependency error: {e}")
            return False
        except Exception as e:
            print(f"ERROR: Uninstallation failed: {e}")
            return False

    def get_status(self) -> Dict[str, Dict]:
        """Get status of all services."""
        status = {}
        for name, service in self._instances.items():
            status[name] = service.get_info()
        return status

    def validate_all(self) -> bool:
        """Validate all services."""
        print("Validating all services...")
        all_healthy = True

        for name, service in self._instances.items():
            if not service.config.enabled:
                continue

            print(f"Checking {name}...")
            health = service.get_health()

            if health.value == "healthy":
                print(f"  [PASS] {name}: {health.value}")
            elif health.value == "degraded":
                print(f"  [WARN] {name}: {health.value}")
                all_healthy = False
            else:
                print(f"  [FAIL] {name}: {health.value}")
                all_healthy = False

        return all_healthy


# Global service registry instance
service_registry = ServiceRegistry()