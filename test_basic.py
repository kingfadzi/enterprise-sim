#!/usr/bin/env python3
"""Basic test to validate Phase 1 implementation."""

import sys
import tempfile
import os
import yaml


def test_imports():
    """Test that all modules can be imported."""
    try:
        from enterprise_sim import EnterpriseSimCLI, main
        from enterprise_sim.core import ConfigManager, ClusterManager
        from enterprise_sim.utils import KubernetesClient, HelmClient
        print("✅ All imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False


def test_config_manager():
    """Test configuration manager functionality."""
    try:
        from enterprise_sim.core.config import ConfigManager

        # Test default configuration
        config_manager = ConfigManager()
        cluster_config = config_manager.get_cluster_config()

        assert cluster_config.name == "enterprise-sim"
        assert cluster_config.workers == 3
        assert cluster_config.registry_port == 5000

        # Test configuration file creation
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config_data = {
                'cluster': {
                    'name': 'test-cluster',
                    'workers': 2
                },
                'services': {
                    'istio': {
                        'enabled': False,
                        'version': '1.19.0'
                    }
                }
            }
            yaml.dump(config_data, f)
            temp_config = f.name

        # Load custom configuration
        config_manager = ConfigManager(temp_config)
        cluster_config = config_manager.get_cluster_config()

        assert cluster_config.name == "test-cluster"
        assert cluster_config.workers == 2

        istio_config = config_manager.get_service_config('istio')
        assert istio_config.enabled == False
        assert istio_config.version == '1.19.0'

        # Cleanup
        os.unlink(temp_config)

        print("✅ ConfigManager tests passed")
        return True

    except Exception as e:
        print(f"❌ ConfigManager test failed: {e}")
        return False


def test_cluster_manager():
    """Test cluster manager basic functionality."""
    try:
        from enterprise_sim.core.config import ClusterConfig
        from enterprise_sim.core.cluster import ClusterManager

        # Create test configuration
        config = ClusterConfig(name="test-cluster", workers=1)
        cluster_manager = ClusterManager(config)

        # Test basic methods exist and are callable
        assert hasattr(cluster_manager, 'exists')
        assert hasattr(cluster_manager, 'create')
        assert hasattr(cluster_manager, 'delete')
        assert hasattr(cluster_manager, 'start')
        assert hasattr(cluster_manager, 'stop')

        print("✅ ClusterManager tests passed")
        return True

    except Exception as e:
        print(f"❌ ClusterManager test failed: {e}")
        return False


def test_k8s_client():
    """Test Kubernetes client functionality."""
    try:
        from enterprise_sim.utils.k8s import KubernetesClient, HelmClient

        # Test client creation
        k8s_client = KubernetesClient()
        helm_client = HelmClient()

        # Test basic methods exist
        assert hasattr(k8s_client, 'apply_manifest')
        assert hasattr(k8s_client, 'get_resource')
        assert hasattr(k8s_client, 'create_namespace')

        assert hasattr(helm_client, 'add_repo')
        assert hasattr(helm_client, 'install')
        assert hasattr(helm_client, 'list_releases')

        print("✅ Kubernetes and Helm client tests passed")
        return True

    except Exception as e:
        print(f"❌ K8s client test failed: {e}")
        return False


def test_cli():
    """Test CLI functionality."""
    try:
        from enterprise_sim.cli import EnterpriseSimCLI

        cli = EnterpriseSimCLI()
        parser = cli.create_parser()

        # Test basic parsing
        args = parser.parse_args(['config', 'init'])
        assert args.command == 'config'
        assert args.config_command == 'init'

        args = parser.parse_args(['cluster', 'create', '--force'])
        assert args.command == 'cluster'
        assert args.cluster_command == 'create'
        assert args.force == True

        print("✅ CLI tests passed")
        return True

    except Exception as e:
        print(f"❌ CLI test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("Running Phase 1 Implementation Tests...")
    print("=" * 50)

    tests = [
        test_imports,
        test_config_manager,
        test_cluster_manager,
        test_k8s_client,
        test_cli
    ]

    passed = 0
    for test in tests:
        if test():
            passed += 1
        print()

    print("=" * 50)
    print(f"Results: {passed}/{len(tests)} tests passed")

    if passed == len(tests):
        print("🎉 All Phase 1 tests passed! Implementation is ready.")
        return True
    else:
        print("⚠️  Some tests failed. Check the implementation.")
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)