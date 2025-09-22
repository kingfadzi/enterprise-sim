"""Base service implementation driven by YAML manifests."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base import BaseService, ServiceHealth
from ..core.config import ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient
from .manifest_def import ServiceManifest, InstallStep, ValidationSpec, WaitForSpec
from ..utils.manifests import render_manifest


class ManifestService(BaseService):
    """Service implementation that follows metadata-defined install steps."""

    def __init__(
        self,
        definition: ServiceManifest,
        config: ServiceConfig,
        k8s_client: KubernetesClient,
        helm_client: HelmClient,
        global_context: Optional[Dict[str, Any]] = None,
    ):
        self.definition = definition
        super().__init__(config, k8s_client, helm_client, global_context)
        self._health_state: ServiceHealth = ServiceHealth.UNKNOWN
        # Apply config defaults if not already provided
        for key, value in self.definition.config_defaults.items():
            self.config.config.setdefault(key, value)

        # Derive gateway default from environment when available
        if 'gateway_name' not in self.config.config:
            env = (self.global_context or {}).get('environment', {})
            domain = env.get('domain', 'localhost')
            env_name = self._derive_env_from_domain(domain)
            self.config.config['gateway_name'] = f"{env_name}-gateway"

    # ------------------------------------------------------------------
    # Properties required by BaseService
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.definition.service_id

    @property
    def namespace(self) -> str:
        return self.definition.namespace or self.global_context.get('service_namespace', self.definition.service_id)

    @property
    def dependencies(self) -> set:
        return set(self.definition.dependencies or [])

    @property
    def helm_chart(self):  # type: ignore[override]
        return None

    def get_helm_values(self):  # type: ignore[override]
        return {}

    def validate_prerequisites(self) -> bool:  # type: ignore[override]
        return True

    def post_install_tasks(self) -> bool:  # type: ignore[override]
        return True

    # ------------------------------------------------------------------
    # Install & uninstall logic based on manifest metadata
    # ------------------------------------------------------------------
    def install(self) -> bool:  # type: ignore[override]
        try:
            for step in self.definition.install:
                if not self._execute_step(step):
                    return False
            return True
        except Exception as exc:  # pragma: no cover - defensive log
            print(f"ERROR: Installation failed for {self.name}: {exc}")
            return False

    def uninstall(self) -> bool:  # type: ignore[override]
        success = True
        for step in reversed(self.definition.install):
            if step.step_type == 'helm' and step.release and step.namespace:
                if not self.helm.uninstall(step.release, step.namespace):
                    success = False
            elif step.step_type == 'manifest' and step.path:
                context = self._build_context(step.context)
                manifest_text = render_manifest(step.path, **context)
                if not self.k8s.delete_manifest(manifest_text, step.namespace or self.namespace):
                    success = False
        return success

    # ------------------------------------------------------------------
    # Validation & health helpers
    # ------------------------------------------------------------------
    def validate(self) -> bool:  # type: ignore[override]
        all_results = []
        for check in self.definition.validations:
            result, message = self._run_validation(check)
            status = "PASS" if result else "FAIL"
            print(f"  [{status}] {message}")
            all_results.append(result)
        return all(all_results) if all_results else True

    def get_health(self) -> ServiceHealth:  # type: ignore[override]
        if not self.definition.validations:
            return ServiceHealth.UNKNOWN
        if self.validate():
            return ServiceHealth.HEALTHY
        return ServiceHealth.DEGRADED

    def get_endpoints(self, domain: str) -> List[Dict[str, str]]:  # type: ignore[override]
        endpoints: List[Dict[str, str]] = []
        for endpoint in self.definition.endpoints:
            context = self._build_context({'domain': domain})
            name = self._template_string(endpoint.name, context)
            url = self._template_string(endpoint.url, context)
            endpoints.append({'name': name, 'url': url, 'type': endpoint.type})
        return endpoints

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _execute_step(self, step: InstallStep) -> bool:
        if step.step_type == 'helm':
            return self._execute_helm_step(step)
        if step.step_type == 'manifest':
            return self._execute_manifest_step(step)
        print(f"WARNING: Unknown install step type '{step.step_type}' for {self.name}")
        return True

    def _execute_manifest_step(self, step: InstallStep) -> bool:
        if not step.path:
            print(f"WARNING: Manifest step for {self.name} missing path")
            return True
        context = self._build_context(step.context)
        manifest_text = render_manifest(step.path, **context)
        ns = step.namespace or context.get('namespace') or self.namespace
        if not self.k8s.apply_manifest(manifest_text, ns):
            print(f"ERROR: Failed to apply manifest {step.path}")
            return False
        return self._handle_wait_conditions(step.wait_for)

    def _execute_helm_step(self, step: InstallStep) -> bool:
        if not step.chart or not step.release or not step.namespace:
            print(f"ERROR: Helm step for {self.name} requires chart, release, and namespace")
            return False

        repo = step.repo or {}
        if repo.get('name') and repo.get('url'):
            if not self.helm.add_repo(repo['name'], repo['url']):
                return False
        if not self.helm.update_repos():
            return False

        values = step.values or {}
        if not self.helm.install(
            release_name=step.release,
            chart=f"{repo.get('name', '')}/{step.chart}" if repo.get('name') else step.chart,
            namespace=step.namespace,
            values=values,
        ):
            return False
        return self._handle_wait_conditions(step.wait_for)

    def _handle_wait_conditions(self, waits: List[WaitForSpec]) -> bool:
        for wait in waits:
            if not self._wait_for_condition(wait):
                return False
        return True

    def _wait_for_condition(self, wait: WaitForSpec) -> bool:
        timeout = wait.timeout or 300
        if wait.type == 'deployment' and wait.name and wait.namespace:
            return self.k8s.wait_for_deployment(wait.name, wait.namespace, timeout)
        if wait.type == 'custom_resource' and all([wait.group, wait.version, wait.plural, wait.name, wait.namespace]):
            end_time = time.time() + timeout
            while time.time() < end_time:
                try:
                    resource = self.k8s.custom_objects.get_namespaced_custom_object(
                        group=wait.group,
                        version=wait.version,
                        namespace=wait.namespace,
                        plural=wait.plural,
                        name=wait.name,
                    )
                    if self._check_condition(resource, wait.condition):
                        return True
                except Exception:
                    pass
                time.sleep(5)
            print(f"Timeout waiting for custom resource {wait.name} in {wait.namespace}")
            return False
        return True

    def _check_condition(self, resource: Dict[str, Any], condition: Optional[Dict[str, Any]]) -> bool:
        if not condition:
            return True
        path = condition.get('path')
        expected = condition.get('equals')
        if not path:
            return True
        current = self._get_nested(resource, path)
        return current == expected

    def _run_validation(self, spec: ValidationSpec) -> (bool, str):
        if spec.type == 'deployment' and spec.name and spec.namespace:
            summary = self.k8s.summarize_deployment_readiness(spec.name, spec.namespace)
            if not summary:
                return False, f"Deployment {spec.name} missing in {spec.namespace}"
            desired = summary['desired_replicas'] or summary['effective_total']
            ready = summary['effective_ready']
            ok = bool(desired) and ready >= desired
            return ok, f"Deployment {spec.name} ready ({ready}/{desired})"

        if spec.type == 'custom_resource' and all([spec.group, spec.version, spec.plural, spec.name, spec.namespace]):
            try:
                resource = self.k8s.custom_objects.get_namespaced_custom_object(
                    group=spec.group,
                    version=spec.version,
                    namespace=spec.namespace,
                    plural=spec.plural,
                    name=spec.name,
                )
                ok = self._check_condition(resource, spec.condition)
                msg = f"Custom resource {spec.name} in {spec.namespace}"
                if spec.condition:
                    msg += f" condition {spec.condition.get('path')} == {spec.condition.get('equals')}"
                return ok, msg
            except Exception as exc:
                return False, f"Custom resource {spec.name} check failed: {exc}"

        return False, f"Unsupported validation type {spec.type}"

    def _build_context(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context: Dict[str, Any] = {}
        env = self.global_context.get('environment', {}) if self.global_context else {}

        # Base entries
        context['service_name'] = self.name
        context['namespace'] = overrides.get('namespace') if overrides and 'namespace' in overrides else self.namespace
        context['domain'] = env.get('domain', 'localhost')
        context['env'] = env

        # Include service config values at top level for easy templating
        for key, value in self.config.config.items():
            context.setdefault(key, value)

        # Include overrides with resolution
        if overrides:
            for key, value in overrides.items():
                context[key] = self._resolve_context_value(value, context)

        # Also expose config defaults defined in manifest
        for key, value in self.definition.config_defaults.items():
            context.setdefault(key, value)

        return context

    def _resolve_context_value(self, value: Any, base: Dict[str, Any]) -> Any:
        if not isinstance(value, str) or not value.startswith('@'):
            return value

        source, *rest = value[1:].split('|', 1)
        default = rest[0] if rest else None

        if source.startswith('config.'):
            key = source[len('config.'):]
            return self.config.config.get(key, default)
        if source.startswith('env.'):
            key = source[len('env.'):]
            env = self.global_context.get('environment', {}) if self.global_context else {}
            return env.get(key, default)
        if source.startswith('service.'):
            key = source[len('service.') :]
            return {
                'name': self.name,
                'namespace': self.namespace,
            }.get(key, default)
        return default

    def _template_string(self, template: str, context: Dict[str, Any]) -> str:
        from string import Template

        return Template(template).safe_substitute(context)

    @staticmethod
    def _get_nested(data: Dict[str, Any], path: str) -> Any:
        current: Any = data
        for part in path.split('.'):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    @staticmethod
    def _derive_env_from_domain(domain: str) -> str:
        if not domain or domain in ("localhost", "127.0.0.1"):
            return "local"
        parts = domain.split('.')
        if len(parts) < 2:
            return "local"
        return parts[0]
