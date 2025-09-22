"""Service registry and dependency management."""

from typing import Any, Dict, List, Set, Type, Optional
import time
from .base import BaseService, ServiceStatus


class DependencyError(Exception):
    """Raised when dependency resolution fails."""
    pass


class ServiceRegistry:
    """Manages service registration and dependency resolution."""

    def __init__(self):
        self._services: Dict[str, Type[BaseService]] = {}
        self._factories: Dict[str, Any] = {}
        self._instances: Dict[str, BaseService] = {}

    def register(self, service_class: Type[BaseService]):
        """Register a service class."""
        # Get service name from a temporary instance
        temp_instance = service_class.__new__(service_class)
        service_name = temp_instance.name

        self._services[service_name] = service_class
        print(f"Registered service: {service_name}")

    def create_instance(
        self,
        service_name: str,
        config,
        k8s_client,
        helm_client,
        global_context: Optional[Dict] = None,
    ) -> BaseService:
        """Create service instance."""
        if service_name in self._instances:
            return self._instances[service_name]

        if service_name in self._services:
            service_class = self._services[service_name]
            try:
                instance = service_class(config, k8s_client, helm_client, global_context)
            except TypeError:
                instance = service_class(config, k8s_client, helm_client)
        elif service_name in self._factories:
            instance = self._factories[service_name](config, k8s_client, helm_client, global_context)
        else:
            raise ValueError(f"Service {service_name} not registered")

        self._instances[service_name] = instance
        return instance

    def register_manifest(self, manifest, factory):
        self._factories[manifest.service_id] = factory

    def registered_services(self) -> List[str]:
        return list(self._services.keys()) + list(self._factories.keys())

    def get_service(self, service_name: str) -> Optional[BaseService]:
        """Get service instance."""
        return self._instances.get(service_name)

    def get_all_services(self) -> Dict[str, BaseService]:
        """Get all service instances."""
        return self._instances.copy()

    def clear_instances(self):
        """Clear all service instances."""
        self._instances.clear()

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
        # Kahn's algorithm with adjacency from dependency -> dependents
        in_degree: Dict[str, int] = {}
        adjacency: Dict[str, Set[str]] = {}

        for node, dependencies in graph.items():
            in_degree.setdefault(node, 0)
            for dependency in dependencies:
                in_degree[node] = in_degree.get(node, 0) + 1
                adjacency.setdefault(dependency, set()).add(node)
                in_degree.setdefault(dependency, 0)

        for node in graph:
            adjacency.setdefault(node, set())

        queue = [node for node, degree in in_degree.items() if degree == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            for dependent in adjacency.get(node, set()):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(in_degree):
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

                # Wait for service health before moving on
                print(f"Waiting for {service_name} to be ready...")
                if not service.wait_for_ready(timeout=600):
                    elapsed = time.time() - start_time
                    print(f"❌ {service_name} failed to become ready within 600s (elapsed {elapsed:.1f}s)")
                    return False

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

    def get_status(self, domain: str) -> Dict[str, Dict]:
        """Get status of all services."""
        status = {}
        for name, service in self._instances.items():
            status[name] = service.get_info(domain)
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
