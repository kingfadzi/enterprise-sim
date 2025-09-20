"""Main CLI interface for enterprise simulation."""

import argparse
import sys
from typing import Optional

from .core.config import ConfigManager
from .core.cluster import ClusterManager
from .core.validation import ServiceValidator
from .utils.k8s import KubernetesClient, HelmClient
from .services import service_registry, IstioService, CertManagerService, OpenEBSService, MinioService, SampleAppService
from .security import CertificateManager, PolicyManager, GatewayManager


class EnterpriseSimCLI:
    """Main CLI application for enterprise simulation."""

    def __init__(self):
        self.config_manager: Optional[ConfigManager] = None
        self.cluster_manager: Optional[ClusterManager] = None
        self.k8s_client: Optional[KubernetesClient] = None
        self.helm_client: Optional[HelmClient] = None
        self.validator: Optional[ServiceValidator] = None
        self.cert_manager: Optional[CertificateManager] = None
        self.policy_manager: Optional[PolicyManager] = None
        self.gateway_manager: Optional[GatewayManager] = None

    def _initialize(self, config_file: Optional[str] = None):
        """Initialize managers and clients."""
        try:
            self.config_manager = ConfigManager(config_file)
            cluster_config = self.config_manager.get_cluster_config()
            self.cluster_manager = ClusterManager(cluster_config)
            self.k8s_client = KubernetesClient()
            self.helm_client = HelmClient()
            self.validator = ServiceValidator(self.k8s_client)

            # Initialize security managers
            self.cert_manager = CertificateManager(self.k8s_client)
            self.policy_manager = PolicyManager(self.k8s_client)
            self.gateway_manager = GatewayManager(self.k8s_client)

            # Register services
            service_registry.register(IstioService)
            service_registry.register(CertManagerService)
            service_registry.register(OpenEBSService)
            service_registry.register(MinioService)
            service_registry.register(SampleAppService)

            # Create service instances
            for service_name, service_config in self.config_manager.config.services.items():
                if service_name == 'istio':
                    service_registry.create_instance(service_name, service_config, self.k8s_client, self.helm_client)
                elif service_name == 'cert-manager':
                    service_registry.create_instance(service_name, service_config, self.k8s_client, self.helm_client)
                elif service_name == 'storage':
                    service_registry.create_instance(service_name, service_config, self.k8s_client, self.helm_client)
                elif service_name == 'minio':
                    service_registry.create_instance(service_name, service_config, self.k8s_client, self.helm_client)
                elif service_name == 'sample-app':
                    service_registry.create_instance(service_name, service_config, self.k8s_client, self.helm_client)

        except Exception as e:
            print(f"Failed to initialize: {e}")
            sys.exit(1)

    def create_cluster(self, args):
        """Create k3d cluster."""
        self._initialize(args.config)

        print("Creating enterprise simulation cluster...")
        print("=" * 50)

        # Show configuration
        cluster_config = self.config_manager.get_cluster_config()
        print(f"Cluster Configuration:")
        print(f"   Name: {cluster_config.name}")
        print(f"   Workers: {cluster_config.workers}")
        print(f"   Registry: localhost:{cluster_config.registry_port}")
        print(f"   HTTP: localhost:{cluster_config.ingress_http_port}")
        print(f"   HTTPS: localhost:{cluster_config.ingress_https_port}")
        print()

        # Create cluster
        print("Step 1/4: Creating k3d cluster")
        if self.cluster_manager.create(force=args.force):
            print()

            print("Step 2/4: Configuring kubectl access")
            if self.cluster_manager.get_kubeconfig():
                print("kubectl configured successfully")
            else:
                print("WARNING: kubectl configuration failed, but continuing...")
            print()

            print("Step 3/4: Gathering cluster information")
            status = self.cluster_manager.get_status()
            if status:
                print(f"   Cluster: {status['name']}")
                print(f"   Servers: {status.get('serversCount', 'N/A')}")
                print(f"   Agents: {status.get('agentsCount', 'N/A')}")

            registry = self.cluster_manager.get_registry_info()
            if registry:
                print(f"   Registry: {registry['host']}")
            print()

            if args.validate:
                print("Step 4/4: Validating cluster")
                if self.cluster_manager.validate_cluster():
                    print("Cluster validation passed")
                else:
                    print("ERROR: Cluster validation failed")
                    return False
            else:
                print("Step 4/4: Skipping validation (use --validate to enable)")

            print()
            print("Enterprise simulation cluster is ready!")
            print(f"   Access via: kubectl --context k3d-{cluster_config.name}")
            print(f"   HTTP:  http://localhost:{cluster_config.ingress_http_port}")
            print(f"   HTTPS: https://localhost:{cluster_config.ingress_https_port}")

            return True
        else:
            print("ERROR: Failed to create cluster")
            return False

    def delete_cluster(self, args):
        """Delete k3d cluster."""
        self._initialize(args.config)

        if not args.force:
            cluster_name = self.config_manager.get_cluster_config().name
            confirm = input(f"Are you sure you want to delete cluster '{cluster_name}'? (y/N): ")
            if confirm.lower() != 'y':
                print("Deletion cancelled")
                return True

        print("Deleting enterprise simulation cluster...")
        if self.cluster_manager.delete():
            print("✅ Cluster deleted successfully")
            return True
        else:
            print("❌ Failed to delete cluster")
            return False

    def start_cluster(self, args):
        """Start existing cluster."""
        self._initialize(args.config)

        print("Starting enterprise simulation cluster...")
        if self.cluster_manager.start():
            print("✅ Cluster started successfully")
            self.cluster_manager.get_kubeconfig()
            return True
        else:
            print("❌ Failed to start cluster")
            return False

    def stop_cluster(self, args):
        """Stop running cluster."""
        self._initialize(args.config)

        print("Stopping enterprise simulation cluster...")
        if self.cluster_manager.stop():
            print("✅ Cluster stopped successfully")
            return True
        else:
            print("❌ Failed to stop cluster")
            return False

    def status(self, args):
        """Show cluster and services status."""
        self._initialize(args.config)

        # Cluster status
        status = self.cluster_manager.get_status()
        if status:
            print(f"Cluster: {status['name']}")
            print(f"Status: {status.get('status', 'Unknown')}")
            print(f"Servers: {status.get('serversCount', 'N/A')}")
            print(f"Agents: {status.get('agentsCount', 'N/A')}")
        else:
            print("Cluster: Not found")
            return True

        # Registry info
        registry = self.cluster_manager.get_registry_info()
        if registry:
            print(f"Registry: {registry['host']}")
        else:
            print("Registry: Not available")

        # Service status (basic implementation)
        if args.verbose:
            print("\nServices:")
            for service_name, service_config in self.config_manager.config.services.items():
                enabled = "✅" if service_config.enabled else "❌"
                print(f"  {service_name}: {enabled} (v{service_config.version})")

        return True

    def config_init(self, args):
        """Initialize configuration file."""
        config_file = args.output or 'enterprise-sim.yaml'

        # Create default configuration
        config_manager = ConfigManager()
        config_manager.save_config(config_file)

        print(f"✅ Configuration initialized: {config_file}")
        print("Edit the configuration file to customize your setup")
        return True

    def config_show(self, args):
        """Show current configuration."""
        self._initialize(args.config)

        print("Current Configuration:")
        print(f"Cluster: {self.config_manager.config.cluster.name}")
        print(f"Workers: {self.config_manager.config.cluster.workers}")
        print(f"Registry Port: {self.config_manager.config.cluster.registry_port}")
        print(f"Ingress HTTP: {self.config_manager.config.cluster.ingress_http_port}")
        print(f"Ingress HTTPS: {self.config_manager.config.cluster.ingress_https_port}")

        print("\nServices:")
        for name, service in self.config_manager.config.services.items():
            status = "enabled" if service.enabled else "disabled"
            print(f"  {name}: {status} (v{service.version})")

        return True

    def validate(self, args):
        """Validate cluster and services."""
        self._initialize(args.config)

        print("Validating enterprise simulation environment...")

        # Validate cluster
        if not self.cluster_manager.validate_cluster():
            print("❌ Cluster validation failed")
            return False

        print("✅ Cluster validation passed")

        # TODO: Add service validation when services are implemented
        if args.services:
            print("Service validation not yet implemented")

        return True

    def install_services(self, args):
        """Install services."""
        self._initialize(args.config)

        services_to_install = args.services if args.services else list(self.config_manager.config.services.keys())
        enabled_services = [s for s in services_to_install if self.config_manager.is_service_enabled(s)]

        if not enabled_services:
            print("No enabled services to install")
            return True

        print(f"Installing services: {', '.join(enabled_services)}")

        if service_registry.install_services(enabled_services):
            print("All services installed successfully!")

            # Show accessible URLs for installed services
            print("\n📍 Service Endpoints:")
            print("=" * 50)
            status_info = service_registry.get_status()
            for service_name in enabled_services:
                if service_name in status_info:
                    info = status_info[service_name]
                    if info['endpoints']:
                        print(f"\n🔗 {service_name.upper()} Service:")
                        for endpoint in info['endpoints']:
                            if 'External' in endpoint['type']:
                                print(f"   ✅ {endpoint['name']}: {endpoint['url']}")
                            else:
                                print(f"   🔒 {endpoint['name']}: {endpoint['url']} (Internal)")
            print()
            return True
        else:
            print("ERROR: Some services failed to install")
            return False

    def uninstall_services(self, args):
        """Uninstall services."""
        self._initialize(args.config)

        services_to_uninstall = args.services if args.services else list(self.config_manager.config.services.keys())

        if not services_to_uninstall:
            print("No services specified for uninstallation")
            return True

        print(f"Uninstalling services: {', '.join(services_to_uninstall)}")

        if service_registry.uninstall_services(services_to_uninstall):
            print("All services uninstalled successfully")
            return True
        else:
            print("ERROR: Some services failed to uninstall")
            return False

    def service_status(self, args):
        """Show service status."""
        self._initialize(args.config)

        print("Service Status:")
        print("=" * 50)

        status_info = service_registry.get_status()
        for service_name, info in status_info.items():
            enabled = "ENABLED" if info['enabled'] else "DISABLED"
            installed = "INSTALLED" if info['installed'] else "NOT_INSTALLED"
            health = info['health'].upper()

            print(f"[{enabled}] [{installed}] [{health}] {service_name}")
            print(f"    Version: {info['version']}")
            print(f"    Status: {info['status']}")
            print(f"    Health: {info['health']}")

            if info['endpoints']:
                print(f"    Endpoints:")
                for endpoint in info['endpoints']:
                    print(f"      - {endpoint['name']}: {endpoint['url']} ({endpoint['type']})")

            if args.verbose and info['dependencies']:
                print(f"    Dependencies: {', '.join(info['dependencies'])}")
            print()

        return True

    def validate_services(self, args):
        """Validate services."""
        self._initialize(args.config)

        print("Validating Enterprise Simulation Environment")
        print("=" * 60)

        all_passed = True

        # Validate cluster basics
        print("\nCluster Validation")
        cluster_results = self.validator.validate_cluster_basics()
        for result in cluster_results:
            print(f"  {result}")
            if not result.success:
                all_passed = False

        # Validate specific services if requested
        if args.services:
            for service_name in args.services:
                service = service_registry.get_service(service_name)
                if not service:
                    print(f"\nERROR: Service {service_name} not found")
                    all_passed = False
                    continue

                print(f"\n{service_name.title()} Validation")
                if service_name == 'istio':
                    results = self.validator.validate_istio_mesh()
                else:
                    results = self.validator.validate_service_deployment(service_name, service.namespace)

                for result in results:
                    print(f"  {result}")
                    if not result.success:
                        all_passed = False

        # Print comprehensive service settings and status
        print("\nService Configuration & Status")
        print("=" * 60)

        status_info = service_registry.get_status()
        for service_name, info in status_info.items():
            enabled = "✅ ENABLED" if info['enabled'] else "❌ DISABLED"
            installed = "✅ INSTALLED" if info['installed'] else "❌ NOT INSTALLED"
            health = f"🟢 {info['health'].upper()}" if info['health'] == 'healthy' else f"🔴 {info['health'].upper()}"

            print(f"\n📋 {service_name.upper()} SERVICE")
            print(f"   Status: {enabled} | {installed} | {health}")
            print(f"   Version: {info['version']}")

            # Print service configuration details
            service = service_registry.get_service(service_name)
            if service and hasattr(service, 'config') and service.config.config:
                print(f"   Configuration:")
                for key, value in service.config.config.items():
                    print(f"     {key}: {value}")

            # Print dependencies
            if info['dependencies']:
                print(f"   Dependencies: {', '.join(info['dependencies'])}")
            else:
                print(f"   Dependencies: None")

            # Print endpoints with detailed info
            if info['endpoints']:
                print(f"   Endpoints:")
                for endpoint in info['endpoints']:
                    endpoint_type = "🌐" if 'External' in endpoint['type'] else "🔒"
                    print(f"     {endpoint_type} {endpoint['name']}: {endpoint['url']}")
                    print(f"        Type: {endpoint['type']}")
            else:
                print(f"   Endpoints: None configured")

        # Print cluster configuration
        print(f"\n🏗️  CLUSTER CONFIGURATION")
        cluster_config = self.config_manager.get_cluster_config()
        print(f"   Name: {cluster_config.name}")
        print(f"   Workers: {cluster_config.workers}")
        print(f"   API Port: {cluster_config.api_port}")
        print(f"   HTTP Port: {cluster_config.ingress_http_port}")
        print(f"   HTTPS Port: {cluster_config.ingress_https_port}")
        print(f"   Registry Port: {cluster_config.registry_port}")

        # Print environment configuration
        print(f"\n🌍 ENVIRONMENT CONFIGURATION")
        env_config = self.config_manager.config.environment
        if env_config:
            for key, value in env_config.items():
                print(f"   {key}: {value}")
        else:
            print("   No environment variables configured")

        print("\n" + "=" * 60)
        if all_passed:
            print("✅ All validations passed!")
        else:
            print("⚠️  WARNING: Some validations failed. Check the details above.")

        return all_passed

    def setup_certificates(self, args):
        """Setup TLS certificates."""
        self._initialize(args.config)

        mode = getattr(args, 'mode', 'self-signed')
        domain = getattr(args, 'domain', 'localhost')
        production = getattr(args, 'production', False)
        staging = not production  # Default to staging unless --production is specified

        # Update cert manager with domain
        self.cert_manager.domain = domain
        self.cert_manager.wildcard_domain = f"*.{domain}"
        self.cert_manager.secret_name = f"{domain.replace('.', '-')}-tls"

        print(f"Setting up TLS certificates for domain: {domain}")
        if mode == "letsencrypt":
            env_name = "production" if production else "staging"
            print(f"Let's Encrypt environment: {env_name}")

        if self.cert_manager.setup_certificates(mode, staging):
            print("TLS certificates setup completed")
            return True
        else:
            print("ERROR: TLS certificate setup failed")
            return False

    def setup_regions(self, args):
        """Setup regions with zero-trust policies."""
        self._initialize(args.config)

        regions = args.regions or ['us', 'eu', 'ap']
        print(f"Setting up regions with zero-trust policies: {', '.join(regions)}")

        if self.policy_manager.setup_region_security(regions):
            if self.policy_manager.setup_istio_system_policies():
                print("Region security setup completed")
                return True

        print("ERROR: Region security setup failed")
        return False

    def setup_gateway(self, args):
        """Setup wildcard ingress gateway."""
        self._initialize(args.config)

        domain = getattr(args, 'domain', 'localhost')

        # Update gateway manager with domain
        self.gateway_manager.domain = domain
        self.gateway_manager.wildcard_domain = f"*.{domain}"
        self.gateway_manager.gateway_name = f"{domain.replace('.', '-')}-gateway"
        self.gateway_manager.secret_name = f"{domain.replace('.', '-')}-tls"

        print(f"Setting up wildcard gateway for domain: {domain}")

        if self.gateway_manager.create_wildcard_gateway():
            print("Wildcard gateway setup completed")
            return True
        else:
            print("ERROR: Gateway setup failed")
            return False

    def security_status(self, args):
        """Show security status."""
        self._initialize(args.config)

        print("Security Status:")
        print("=" * 50)

        # Certificate status
        cert_info = self.cert_manager.get_certificate_info()
        if cert_info:
            print(f"TLS Certificate: {self.cert_manager.secret_name}")
            print(f"  Subject: {cert_info.get('subject', 'Unknown')}")
            print(f"  Valid Until: {cert_info.get('not_after', 'Unknown')}")
            if 'san' in cert_info:
                print(f"  Domains: {', '.join(cert_info['san'])}")
        else:
            print("TLS Certificate: Not found")

        print()

        # Gateway status
        gateway_status = self.gateway_manager.get_gateway_status()
        if gateway_status.get('exists'):
            print(f"Gateway: {gateway_status['name']}")
            print(f"  Hosts: {', '.join(gateway_status['hosts'])}")
            if gateway_status['endpoints']:
                print(f"  Endpoints: {', '.join(gateway_status['endpoints'])}")
        else:
            print("Gateway: Not found")

        print()

        # Virtual services
        virtual_services = self.gateway_manager.list_virtual_services()
        print(f"Virtual Services: {len(virtual_services)}")
        for vs in virtual_services:
            print(f"  {vs['name']} ({vs['namespace']}): {', '.join(vs['hosts'])}")

        return True

    def validate_security(self, args):
        """Validate security configuration."""
        self._initialize(args.config)

        print("Validating security configuration...")
        print("=" * 50)

        all_valid = True

        # Validate certificates
        print("Certificate Validation:")
        if self.cert_manager.validate_certificate():
            print("  [PASS] TLS certificate is valid")
        else:
            print("  [FAIL] TLS certificate validation failed")
            all_valid = False

        print()

        # Validate gateway
        print("Gateway Validation:")
        if self.gateway_manager.validate_gateway_connectivity():
            print("  [PASS] Gateway connectivity is valid")
        else:
            print("  [FAIL] Gateway validation failed")
            all_valid = False

        print()

        # Validate policies if regions specified
        if hasattr(args, 'regions') and args.regions:
            print("Policy Validation:")
            if self.policy_manager.validate_policies(args.regions):
                print("  [PASS] Zero-trust policies are valid")
            else:
                print("  [FAIL] Policy validation failed")
                all_valid = False

        print("=" * 50)
        if all_valid:
            print("All security validations passed")
        else:
            print("WARNING: Some security validations failed")

        return all_valid

    def full_up(self, args):
        """Install complete enterprise platform (orchestration command)."""
        print("Enterprise Platform Full Installation")
        print("=" * 50)

        if not args.force:
            print("This will install the complete enterprise platform including:")
            print("  - Cluster infrastructure")
            print("  - Istio service mesh")
            print("  - Certificate management")
            print("  - Enterprise storage (OpenEBS)")
            print("  - Object storage (MinIO)")
            print("  - Sample application")
            print("  - Security policies and routing")
            print()

            response = input("Continue with full installation? (y/N): ")
            if response.lower() != 'y':
                print("Installation cancelled")
                return True

        self._initialize(args.config)

        try:
            # Step 1: Ensure cluster is running
            print("\nStep 1: Cluster Infrastructure")
            print("-" * 30)
            if not self.cluster_manager.exists():
                print("Creating k3d cluster...")
                if not self.cluster_manager.create():
                    print("ERROR: Failed to create cluster")
                    return False
            else:
                print("SUCCESS: Cluster already running")

            # Step 2: Install all services in dependency order
            print("\nStep 2: Platform Services")
            print("-" * 30)

            # Install services with proper dependency resolution
            service_order = ['cert-manager', 'storage', 'istio', 'minio', 'sample-app']
            enabled_services = [s for s in service_order if self.config_manager.is_service_enabled(s)]

            if not enabled_services:
                print("WARNING: No services enabled in configuration")
                return True

            print(f"Installing services: {', '.join(enabled_services)}")

            # Install cert-manager first and setup certificates
            if 'cert-manager' in enabled_services:
                print(f"\nInstalling cert-manager...")
                if not service_registry.install_services(['cert-manager']):
                    print(f"ERROR: Failed to install cert-manager")
                    return False
                print(f"SUCCESS: cert-manager installed successfully")

                # Setup certificates immediately after cert-manager
                print("Setting up SSL certificates for all services...")
                domain = self.config_manager.config.environment.get('domain', 'localhost')

                # Determine certificate mode based on domain and CloudFlare credentials
                if domain == 'localhost':
                    cert_mode = 'self-signed'
                elif self.cert_manager._has_cloudflare_credentials():
                    cert_mode = 'letsencrypt'
                else:
                    cert_mode = 'self-signed'
                    print("WARNING: No CloudFlare credentials found, using self-signed certificates")

                cert_staging = not getattr(args, 'prod', False)

                # Update cert manager with correct domain configuration
                self.cert_manager.domain = domain
                self.cert_manager.wildcard_domain = f"*.{domain}"
                self.cert_manager.secret_name = f"{domain.replace('.', '-')}-tls"

                if not self.cert_manager.setup_certificates(cert_mode, cert_staging):
                    print("ERROR: Certificate setup failed")
                    return False
                else:
                    print("SUCCESS: SSL certificates configured")

                # Remove cert-manager from the list since it's already installed
                enabled_services = [s for s in enabled_services if s != 'cert-manager']

            # Install remaining services in correct dependency order
            if enabled_services:
                # Ensure correct installation order: storage -> istio -> minio -> sample-app
                correct_order = ['storage', 'istio', 'minio', 'sample-app']
                ordered_services = [s for s in correct_order if s in enabled_services]

                for service_name in ordered_services:
                    print(f"\nInstalling {service_name}...")
                    if not service_registry.install_services([service_name]):
                        print(f"ERROR: Failed to install {service_name}")
                        return False
                    print(f"SUCCESS: {service_name} installed successfully")

                print("SUCCESS: All services installed")

            # Step 3: Security and routing setup
            print("\nStep 3: Security & Routing")
            print("-" * 30)

            domain = self.config_manager.config.environment.get('domain', 'localhost')

            # Setup gateway
            print("Setting up security gateway...")
            if not self.gateway_manager.setup_wildcard_gateway(domain=domain):
                print("WARNING: Gateway setup encountered issues")
            else:
                print("SUCCESS: Security gateway configured")

            # Step 4: Validation
            print("\nStep 4: Validation")
            print("-" * 30)

            # Run comprehensive validation
            if not self.validate_services(args):
                print("WARNING: Some validations failed, but installation completed")

            # Show final status
            print("\nINSTALLATION COMPLETE!")
            print("=" * 50)

            # Show all accessible URLs
            print("\n📍 Platform Access URLs:")
            print("-" * 30)
            status_info = service_registry.get_status()

            for service_name, info in status_info.items():
                if info['endpoints'] and info['installed']:
                    print(f"\n🔗 {service_name.upper()}:")
                    for endpoint in info['endpoints']:
                        if 'External' in endpoint['type']:
                            print(f"   ✅ {endpoint['name']}: {endpoint['url']}")

            print(f"\n🌟 Enterprise Platform is ready!")
            print(f"   Primary access: https://us-hello-app.{domain}")

            return True

        except Exception as e:
            print(f"❌ Installation failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def reset(self, args):
        """Reset and reinstall entire platform (orchestration command)."""
        print("Enterprise Platform Reset & Reinstall")
        print("=" * 50)

        if not args.force:
            print("WARNING: This will completely remove and reinstall the platform!")
            print("This will:")
            print("  - Delete ALL services and applications")
            print("  - Remove ALL data and configurations")
            print("  - Destroy and recreate the k3d cluster")
            print("  - Reinstall the complete platform from scratch")
            print()

            response = input("Are you sure you want to reset everything? (y/N): ")
            if response.lower() != 'y':
                print("Reset cancelled")
                return True

        self._initialize(args.config)

        try:
            # Step 1: Uninstall all services
            print("\nStep 1: Removing All Services")
            print("-" * 30)

            all_services = ['sample-app', 'minio', 'storage', 'cert-manager', 'istio']

            for service_name in all_services:
                service = service_registry.get_service(service_name)
                if service and service.is_installed():
                    print(f"Uninstalling {service_name}...")
                    try:
                        service.uninstall()
                    except Exception as e:
                        print(f"WARNING: Error uninstalling {service_name}: {e}")

            # Step 2: Destroy cluster
            print("\nStep 2: Destroying Cluster")
            print("-" * 30)

            if self.cluster_manager.exists():
                print("Destroying k3d cluster...")
                if not self.cluster_manager.delete():
                    print("WARNING: Cluster deletion encountered issues")
                else:
                    print("SUCCESS: Cluster destroyed")
            else:
                print("SUCCESS: Cluster already removed")

            # Step 3: Clean rebuild
            print("\nStep 3: Clean Rebuild")
            print("-" * 30)

            # Brief pause to ensure cleanup
            import time
            print("Waiting for cleanup to complete...")
            time.sleep(5)

            # Now run full-up
            print("Starting full installation...")
            args.force = True  # Skip confirmation for full-up since we already confirmed reset
            return self.full_up(args)

        except Exception as e:
            print(f"❌ Reset failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def create_parser(self):
        """Create argument parser."""
        parser = argparse.ArgumentParser(
            description='Enterprise Simulation Environment Manager',
            formatter_class=argparse.RawDescriptionHelpFormatter
        )

        parser.add_argument('--config', '-c', help='Configuration file path')
        parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

        subparsers = parser.add_subparsers(dest='command', help='Available commands')

        # Cluster commands
        cluster_parser = subparsers.add_parser('cluster', help='Cluster management')
        cluster_subparsers = cluster_parser.add_subparsers(dest='cluster_command')

        # create
        create_parser = cluster_subparsers.add_parser('create', help='Create cluster')
        create_parser.add_argument('--force', '-f', action='store_true', help='Force recreation if exists')
        create_parser.add_argument('--validate', action='store_true', help='Validate after creation')
        create_parser.set_defaults(func=self.create_cluster)

        # delete
        delete_parser = cluster_subparsers.add_parser('delete', help='Delete cluster')
        delete_parser.add_argument('--force', '-f', action='store_true', help='Skip confirmation')
        delete_parser.set_defaults(func=self.delete_cluster)

        # start
        start_parser = cluster_subparsers.add_parser('start', help='Start cluster')
        start_parser.set_defaults(func=self.start_cluster)

        # stop
        stop_parser = cluster_subparsers.add_parser('stop', help='Stop cluster')
        stop_parser.set_defaults(func=self.stop_cluster)

        # Status command
        status_parser = subparsers.add_parser('status', help='Show status')
        status_parser.set_defaults(func=self.status)

        # Config commands
        config_parser = subparsers.add_parser('config', help='Configuration management')
        config_subparsers = config_parser.add_subparsers(dest='config_command')

        # init
        config_init_parser = config_subparsers.add_parser('init', help='Initialize configuration')
        config_init_parser.add_argument('--output', '-o', help='Output file path')
        config_init_parser.set_defaults(func=self.config_init)

        # show
        config_show_parser = config_subparsers.add_parser('show', help='Show configuration')
        config_show_parser.set_defaults(func=self.config_show)

        # Service commands
        service_parser = subparsers.add_parser('service', help='Service management')
        service_subparsers = service_parser.add_subparsers(dest='service_command')

        # install
        install_parser = service_subparsers.add_parser('install', help='Install services')
        install_parser.add_argument('services', nargs='*', help='Services to install (all if not specified)')
        install_parser.set_defaults(func=self.install_services)

        # uninstall
        uninstall_parser = service_subparsers.add_parser('uninstall', help='Uninstall services')
        uninstall_parser.add_argument('services', nargs='*', help='Services to uninstall')
        uninstall_parser.set_defaults(func=self.uninstall_services)

        # status
        service_status_parser = service_subparsers.add_parser('status', help='Show service status')
        service_status_parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed information')
        service_status_parser.set_defaults(func=self.service_status)

        # Security commands
        security_parser = subparsers.add_parser('security', help='Security management')
        security_subparsers = security_parser.add_subparsers(dest='security_command')

        # setup-certificates
        cert_parser = security_subparsers.add_parser('setup-certificates', help='Setup TLS certificates')
        cert_parser.add_argument('--mode', choices=['self-signed', 'letsencrypt'], default='self-signed',
                               help='Certificate mode')
        cert_parser.add_argument('--domain', default='localhost', help='Domain for certificate')
        cert_parser.add_argument('--production', action='store_true',
                               help='Use Let\'s Encrypt production environment (default is staging)')
        cert_parser.set_defaults(func=self.setup_certificates)

        # setup-regions
        regions_parser = security_subparsers.add_parser('setup-regions', help='Setup region security policies')
        regions_parser.add_argument('regions', nargs='*', default=['us', 'eu', 'ap'],
                                   help='Region names to setup')
        regions_parser.set_defaults(func=self.setup_regions)

        # setup-gateway
        gateway_parser = security_subparsers.add_parser('setup-gateway', help='Setup wildcard gateway')
        gateway_parser.add_argument('--domain', default='localhost', help='Domain for gateway')
        gateway_parser.set_defaults(func=self.setup_gateway)

        # status
        security_status_parser = security_subparsers.add_parser('status', help='Show security status')
        security_status_parser.set_defaults(func=self.security_status)

        # validate
        security_validate_parser = security_subparsers.add_parser('validate', help='Validate security')
        security_validate_parser.add_argument('--regions', nargs='*', help='Regions to validate')
        security_validate_parser.set_defaults(func=self.validate_security)

        # Orchestration commands
        fullup_parser = subparsers.add_parser('full-up', help='Install complete enterprise platform')
        fullup_parser.add_argument('--force', '-f', action='store_true',
                                  help='Skip confirmation prompts')
        fullup_parser.add_argument('--prod', '-p', action='store_true',
                                  help='Use production Let\'s Encrypt certificates (default: staging)')
        fullup_parser.set_defaults(func=self.full_up)

        reset_parser = subparsers.add_parser('reset', help='Reset and reinstall entire platform')
        reset_parser.add_argument('--force', '-f', action='store_true',
                                help='Skip confirmation prompts')
        reset_parser.set_defaults(func=self.reset)

        # Validate command
        validate_parser = subparsers.add_parser('validate', help='Validate environment')
        validate_parser.add_argument('--services', nargs='*', help='Specific services to validate')
        validate_parser.set_defaults(func=self.validate_services)

        return parser

    def run(self, argv=None):
        """Run CLI application."""
        parser = self.create_parser()
        args = parser.parse_args(argv)

        if not args.command:
            parser.print_help()
            return True

        # Handle nested commands
        if args.command == 'cluster' and not hasattr(args, 'func'):
            parser.parse_args([args.command, '--help'])
            return True

        if args.command == 'config' and not hasattr(args, 'func'):
            parser.parse_args([args.command, '--help'])
            return True

        if args.command == 'service' and not hasattr(args, 'func'):
            parser.parse_args([args.command, '--help'])
            return True

        if args.command == 'security' and not hasattr(args, 'func'):
            parser.parse_args([args.command, '--help'])
            return True

        try:
            return args.func(args)
        except KeyboardInterrupt:
            print("\nOperation cancelled")
            return False
        except Exception as e:
            if args.verbose:
                import traceback
                traceback.print_exc()
            else:
                print(f"Error: {e}")
            return False


def main():
    """Main entry point."""
    cli = EnterpriseSimCLI()
    success = cli.run()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()