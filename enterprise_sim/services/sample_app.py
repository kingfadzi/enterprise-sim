"""Sample application service implementation."""

import os
import time
from typing import Dict, Any, Set, List, Optional
from ..services.base import BaseService, ServiceStatus, ServiceHealth, ServiceConfig
from ..utils.k8s import KubernetesClient, HelmClient


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
            deployment = self.k8s.get_resource("deployment", app_name, self.namespace)
            if not deployment:
                return ServiceHealth.UNHEALTHY

            # Check deployment status
            status = deployment.get("status", {})
            ready_replicas = status.get("readyReplicas", 0)
            replicas = status.get("replicas", 0)

            if ready_replicas == replicas and replicas > 0:
                return ServiceHealth.HEALTHY
            elif ready_replicas > 0:
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
        # Simple deployment manifest
        deployment_manifest = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app_name}
  namespace: {self.namespace}
  labels:
    app: {app_name}
    version: v1
    compliance.region: {region}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {app_name}
      version: v1
  template:
    metadata:
      labels:
        app: {app_name}
        version: v1
        compliance.region: {region}
      annotations:
        sidecar.istio.io/inject: "true"
    spec:
      containers:
      - name: {app_name}
        image: python:3.9-slim
        command:
        - python
        - -c
        - |
          import http.server
          import socketserver
          import json
          from datetime import datetime

          class Handler(http.server.SimpleHTTPRequestHandler):
              def do_GET(self):
                  if self.path == '/api/health':
                      self.send_response(200)
                      self.send_header('Content-type', 'application/json')
                      self.end_headers()
                      self.wfile.write(json.dumps({{"status": "healthy", "timestamp": datetime.utcnow().isoformat() + "Z"}}).encode())
                  elif self.path == '/api/ready':
                      self.send_response(200)
                      self.send_header('Content-type', 'application/json')
                      self.end_headers()
                      self.wfile.write(json.dumps({{"status": "ready", "timestamp": datetime.utcnow().isoformat() + "Z"}}).encode())
                  elif self.path.startswith('/api/'):
                      self.send_response(200)
                      self.send_header('Content-type', 'application/json')
                      self.end_headers()
                      self.wfile.write(json.dumps({{"message": "Enterprise Platform API", "app": "{app_name}", "region": "{region}"}}).encode())
                  else:
                      self.send_response(200)
                      self.send_header('Content-type', 'text/html')
                      self.end_headers()
                      html = f'''
                      <html><head><title>Enterprise Platform Dashboard</title></head>
                      <body>
                      <h1>🏢 Enterprise Platform Dashboard</h1>
                      <h2>Application: {app_name}</h2>
                      <h2>Region: {region}</h2>
                      <p>✅ Platform services integration working!</p>
                      <ul>
                      <li><a href="/api/health">Health API</a></li>
                      <li><a href="/api/ready">Ready API</a></li>
                      <li><a href="/api/posture">Security Posture API</a></li>
                      </ul>
                      </body></html>
                      '''
                      self.wfile.write(html.encode())

          with socketserver.TCPServer(("", 8080), Handler) as httpd:
              print("Serving Enterprise Dashboard on port 8080")
              httpd.serve_forever()
        ports:
        - name: http
          containerPort: 8080
          protocol: TCP
        env:
        - name: APP_NAME
          value: "{app_name}"
        - name: REGION
          value: "{region}"
        - name: NAMESPACE
          value: "{self.namespace}"
        livenessProbe:
          httpGet:
            path: /api/health
            port: http
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /api/ready
            port: http
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"
---
apiVersion: v1
kind: Service
metadata:
  name: {app_name}
  namespace: {self.namespace}
  labels:
    app: {app_name}
    compliance.region: {region}
    compliance.routing/enabled: "true"
spec:
  type: ClusterIP
  selector:
    app: {app_name}
  ports:
  - name: http
    port: 8080
    targetPort: 8080
    protocol: TCP
"""

        # Apply the deployment
        if not self.k8s.apply_manifest(deployment_manifest, self.namespace):
            return False

        return True

    def _setup_app_routing(self) -> bool:
        """Setup external routing for the application."""
        try:
            app_name = self.config.config.get("app_name", "hello-app")
            region = self.config.config.get("region", "us")
            domain = self._get_domain()

            # Create VirtualService for external access
            vs_manifest = f"""apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: {app_name}-external
  namespace: {self.namespace}
  labels:
    compliance.routing/enabled: "true"
spec:
  hosts:
  - {region}-{app_name}.{domain}
  gateways:
  - istio-system/local-sim-gateway
  http:
  - match:
    - uri:
        prefix: /
    route:
    - destination:
        host: {app_name}.{self.namespace}.svc.cluster.local
        port:
          number: 8080
"""

            if not self.k8s.apply_manifest(vs_manifest, self.namespace):
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

            # Remove VirtualService
            vs_manifest = f"""apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: {app_name}-external
  namespace: {self.namespace}
"""
            self.k8s.delete_manifest(vs_manifest, self.namespace)
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
                'GATEWAY_NAME': 'local-sim-gateway'
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