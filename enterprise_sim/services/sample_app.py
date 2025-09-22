"""Sample application service implementation."""

import os
import time
from typing import Dict, Any, Set, List, Optional
from ..services.base import BaseService, ServiceStatus, ServiceHealth, ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient
from ..utils.manifests import render_manifest


class SampleAppService(BaseService):
    """Sample enterprise platform dashboard application service."""

    def __init__(self, config: ServiceConfig, k8s_client: KubernetesClient, helm_client: HelmClient):
        super().__init__(config, k8s_client, helm_client)

    @property
    def name(self) -> str:
        """Service name."""
        return "sample-app"

    @property
    def namespace(self) -> str:
        """Default namespace for the service."""
        return "sample-app"

    @property
    def dependencies(self) -> Set[str]:
        """Set of service names this service depends on."""
        return {"storage", "minio", "istio"}  # App needs all platform services

    @property
    def helm_chart(self) -> Optional[Dict[str, str]]:
        """Helm chart information."""
        return None  # Uses custom kubectl deployment

    def get_helm_values(self) -> Dict:
        """Get Helm values for sample app installation."""
        return {}  # Not using Helm

    def validate_prerequisites(self) -> bool:
        """Validate prerequisites before installation."""
        # Check if required platform services are available
        try:
            # Verify storage classes exist
            storage_classes = self.k8s.get_resource("storageclass")
            if not storage_classes:
                print("ERROR: No storage classes found")
                return False

            # Check for enterprise storage classes
            enterprise_classes = []
            for sc in storage_classes.get("items", []):
                labels = sc.get("metadata", {}).get("labels", {})
                if labels.get("compliance.storage/managed-by") == "enterprise-sim":
                    enterprise_classes.append(sc.get("metadata", {}).get("name"))

            if not enterprise_classes:
                print("ERROR: No enterprise storage classes found. Please install storage service first.")
                return False

            # Check if MinIO is available
            minio_service = self.k8s.get_resource("service", "minio", "minio-system")
            if not minio_service:
                print("ERROR: MinIO service not found. Please install minio service first.")
                return False

            # Check if Istio is available
            istio_deployment = self.k8s.get_resource("deployment", "istiod", "istio-system")
            if not istio_deployment:
                print("ERROR: Istio not found. Please install istio service first.")
                return False

            print("✅ All prerequisites validated")
            return True

        except Exception as e:
            print(f"ERROR: Cannot validate prerequisites: {e}")
            return False

    def post_install_tasks(self) -> bool:
        """Execute post-installation tasks."""
        try:
            print("Setting up sample application...")

            # Wait for deployment to be ready
            if not self._wait_for_app_ready():
                print("ERROR: Sample app deployment not ready within timeout")
                return False

            print("Creating sample application routing...")
            if not self._setup_app_routing():
                print("ERROR: Failed to setup application routing")
                return False

            print("✅ Sample application setup complete")
            return True

        except Exception as e:
            print(f"ERROR: Exception in post_install_tasks: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_endpoints(self, domain: str) -> List[Dict[str, str]]:
        """Get service endpoints for external access."""
        endpoints = []
        app_name = self.config.config.get("app_name", "hello-app")
        region = self.config.config.get("region", "us")

        # External application endpoint
        endpoints.append({
            "name": "Enterprise Dashboard",
            "url": f"https://{region}-{app_name}.{domain}",
            "type": "External Web Application"
        })

        # API endpoints
        endpoints.append({
            "name": "Platform API",
            "url": f"https://{region}-{app_name}.{domain}/api",
            "type": "External REST API"
        })
        return endpoints

    def get_health(self) -> ServiceHealth:
        """Get sample app service health."""
        try:
            app_name = self.config.config.get("app_name", "hello-app")

            # Check deployment
            summary = self.k8s.summarize_deployment_readiness(app_name, self.namespace)
            if not summary:
                return ServiceHealth.UNHEALTHY

            desired = summary['desired_replicas'] or summary['effective_total']
            ready = summary['effective_ready']

            if desired and ready >= desired:
                return ServiceHealth.HEALTHY
            elif ready > 0:
                return ServiceHealth.DEGRADED
            else:
                return ServiceHealth.UNHEALTHY

        except Exception:
            return ServiceHealth.UNKNOWN

    def uninstall(self) -> bool:
        """Uninstall sample application service."""
        print("Uninstalling sample application...")

        try:
            app_name = self.config.config.get("app_name", "hello-app")

            # Remove routing configuration
            self._remove_app_routing()

            # Delete application resources using kustomize
            if not self._delete_app_resources():
                print("ERROR: Failed to delete application resources")
                return False

            print("Sample application uninstalled")
            return True

        except Exception as e:
            print(f"ERROR: Failed to uninstall sample app: {e}")
            return False

    def validate(self) -> bool:
        """Validate sample application installation and functionality."""
        print(f"Validating {self.name} service...")

        # Check if service is installed
        if not self.is_installed():
            print("  [FAIL] Service not installed")
            return False

        print("  [PASS] Service is installed")

        app_name = self.config.config.get("app_name", "hello-app")

        # Check deployment
        deployment = self.k8s.get_resource("deployment", app_name, self.namespace)
        if not deployment:
            print("  [FAIL] Application deployment not found")
            return False

        print("  [PASS] Application deployment exists")

        # Check service
        service = self.k8s.get_resource("service", app_name, self.namespace)
        if not service:
            print("  [FAIL] Application service not found")
            return False

        print("  [PASS] Application service exists")

        # Check routing
        virtual_service = self.k8s.get_resource("virtualservice", f"{app_name}-external", self.namespace)
        if not virtual_service:
            print("  [FAIL] Application routing not configured")
            return False

        print("  [PASS] Application routing configured")
        print(f"  [PASS] {self.name} service validation completed")
        return True

    def _get_domain(self) -> str:
        """Get domain from config."""
        from ..core.config import ConfigManager
        config_manager = ConfigManager('config.yaml')
        domain = config_manager.config.environment.get('domain')
        if domain is None:
            raise ValueError("Domain not configured in environment settings")
        return domain

    def _derive_env_from_domain(self, domain: str) -> str:
        """Derive environment name from the configured domain."""
        if not domain or domain in {"localhost", "127.0.0.1"}:
            return "local"
        parts = domain.split('.')
        if len(parts) < 2:
            return "local"
        return parts[0]

    def _get_gateway_name(self, domain: str) -> str:
        """Return the gateway name derived from context and domain."""
        if self.global_context:
            gateway_name = self.global_context.get('gateway_name')
            if gateway_name:
                return gateway_name
        env = self._derive_env_from_domain(domain)
        return f"{env}-gateway"

    def _wait_for_app_ready(self, timeout: int = 300) -> bool:
        """Wait for application deployment to be ready."""
        app_name = self.config.config.get("app_name", "hello-app")
        return self.k8s.wait_for_deployment(app_name, self.namespace, timeout)

    def _install_custom(self) -> bool:
        """Install sample application via custom implementation."""
        print("Building and deploying sample application...")

        try:
            # 1. Build the application image
            if not self._build_app_image():
                return False

            # 2. Setup environment configuration
            if not self._setup_app_environment():
                return False

            # 3. Deploy with kubectl kustomize
            if not self._deploy_app_resources():
                return False

            return True

        except Exception as e:
            print(f"ERROR: Failed to install sample app: {e}")
            return False

    def _build_app_image(self) -> bool:
        """Build the sample application Docker image."""
        import subprocess

        try:
            app_name = self.config.config.get("app_name", "hello-app")

            print(f"  Building Docker image for {app_name}...")

            # Get absolute paths
            current_dir = os.getcwd()
            sample_app_dir = os.path.join(current_dir, "sample-app")
            build_script = os.path.join(sample_app_dir, "build.sh")

            if not os.path.exists(sample_app_dir):
                print(f"  [SKIP] Sample app directory not found at {sample_app_dir}")
                return True  # Continue without building

            if not os.path.exists(build_script):
                print(f"  [SKIP] Build script not found at {build_script}")
                return True  # Continue without building

            # Make build script executable and run it
            subprocess.run(['chmod', '+x', build_script], check=True, capture_output=True)
            result = subprocess.run(['bash', build_script],
                                  cwd=sample_app_dir,
                                  check=True,
                                  capture_output=True,
                                  text=True)

            print(f"  ✅ Docker image built successfully")
            return True

        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to build image: {e.stderr}")
            return False
        except Exception as e:
            print(f"  ❌ Build error: {e}")
            return False

    def _setup_app_environment(self) -> bool:
        """Setup application environment configuration."""
        try:
            app_name = self.config.config.get("app_name", "hello-app")
            region = self.config.config.get("region", "us")
            domain = self._get_domain()
            s3_endpoint = f"s3.{domain}"

            # Create .env file from template
            current_dir = os.getcwd()
            env_template_path = os.path.join(current_dir, "sample-app", ".env.template")
            env_path = os.path.join(current_dir, "sample-app", ".env")

            if os.path.exists(env_template_path):
                with open(env_template_path, 'r') as f:
                    env_content = f.read()

                # Replace template variables
                env_content = env_content.replace('APP_NAME=hello-app', f'APP_NAME={app_name}')
                env_content = env_content.replace('REGION=ap', f'REGION={region}')

                # Append platform-injected variables
                env_content += f"\n\n# Platform-injected variables\n"
                env_content += f"S3_ENDPOINT_URL=https://{s3_endpoint}\n"
                env_content += f"DOMAIN={domain}\n"

                with open(env_path, 'w') as f:
                    f.write(env_content)

                print(f"  ✅ Environment configured: {app_name} in {region}")

            return True

        except Exception as e:
            print(f"  ❌ Failed to setup environment: {e}")
            return False

    def _deploy_app_resources(self) -> bool:
        """Deploy application resources using direct manifests."""
        try:
            app_name = self.config.config.get("app_name", "hello-app")
            region = self.config.config.get("region", "us")

            # Create namespace first
            self.k8s.create_namespace(self.namespace)

            # Label namespace for Istio injection
            self.k8s.label_namespace(self.namespace, {"istio-injection": "enabled"})

            # Deploy simplified application directly
            if not self._deploy_simple_app(app_name, region):
                return False

            print(f"  ✅ Application resources deployed")
            return True

        except Exception as e:
            print(f"  ❌ Deployment error: {e}")
            return False

    def _deploy_simple_app(self, app_name: str, region: str) -> bool:
        """Deploy a simple version of the application."""
        deployment_manifest = render_manifest(
            "manifests/sample-app/deployment.yaml",
            app_name=app_name,
            namespace=self.namespace,
            region=region,
        )

        service_manifest = render_manifest(
            "manifests/sample-app/service.yaml",
            app_name=app_name,
            namespace=self.namespace,
            region=region,
        )

        if not self.k8s.apply_manifest(deployment_manifest, self.namespace):
            return False

        if not self.k8s.apply_manifest(service_manifest, self.namespace):
            return False

        return True

    def _setup_app_routing(self) -> bool:
        """Setup external routing for the application."""
        try:
            app_name = self.config.config.get("app_name", "hello-app")
            region = self.config.config.get("region", "us")
            domain = self._get_domain()
            gateway_name = self._get_gateway_name(domain)

            manifest_text = render_manifest(
                "manifests/sample-app/virtualservice.yaml",
                app_name=app_name,
                namespace=self.namespace,
                region=region,
                domain=domain,
                gateway_name=gateway_name,
            )

            if not self.k8s.apply_manifest(manifest_text, self.namespace):
                return False

            print(f"  ✅ External routing configured for {region}-{app_name}.{domain}")
            return True

        except Exception as e:
            print(f"  ❌ Failed to setup routing: {e}")
            return False

    def _remove_app_routing(self) -> bool:
        """Remove application routing configuration."""
        try:
            app_name = self.config.config.get("app_name", "hello-app")

            manifest_text = render_manifest(
                "manifests/sample-app/virtualservice-delete.yaml",
                app_name=app_name,
                namespace=self.namespace,
            )
            self.k8s.delete_manifest(manifest_text, self.namespace)
            return True

        except Exception as e:
            print(f"Warning: Could not remove routing: {e}")
            return False

    def _delete_app_resources(self) -> bool:
        """Delete application resources using kustomize."""
        import subprocess

        try:
            # Export environment variables needed by kustomization
            env = os.environ.copy()
            env.update({
                'NAMESPACE': self.namespace,
                'ROUTE_HOST': self.config.config.get("app_name", "hello-app"),
                'K3S_INGRESS_DOMAIN': self._get_domain(),
                'GATEWAY_NAME': self._get_gateway_name(self._get_domain())
            })

            # Delete resources using kubectl kustomize
            sample_app_path = os.path.join(os.getcwd(), 'sample-app')
            cmd = ['kubectl', 'delete', '-k', sample_app_path]
            result = subprocess.run(cmd,
                                  env=env,
                                  check=False,  # Don't fail if resources don't exist
                                  capture_output=True,
                                  text=True)

            return True

        except Exception as e:
            print(f"Warning: Could not delete resources: {e}")
            return False

    def _is_installed_custom(self) -> bool:
        """Check if sample application is installed."""
        try:
            app_name = self.config.config.get("app_name", "hello-app")
            deployment = self.k8s.get_resource("deployment", app_name, self.namespace)
            return deployment is not None
        except:
            return False
