#!/usr/bin/env python3
"""Basic test to validate Phase 1 implementation."""

import sys
import tempfile
import os
import yaml
from unittest.mock import MagicMock, patch


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

        # Test configuration file creation and loading first
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config_data = {
                'cluster': {
                    'name': 'test-cluster',
                    'workers': 2,
                    'registry_port': 5001
                },
                'services': {
                    'istio': {
                        'enabled': False,
                        'version': '1.19.0'
                    }
                }
            }
            yaml.dump(config_data, f)
            temp_config_path = f.name

        # Load custom configuration from the temporary file
        config_manager_custom = ConfigManager(config_file=temp_config_path)
        cluster_config_custom = config_manager_custom.get_cluster_config()

        assert cluster_config_custom.name == "test-cluster"
        assert cluster_config_custom.workers == 2
        assert cluster_config_custom.registry_port == 5001

        istio_config = config_manager_custom.get_service_config('istio')
        assert not istio_config.enabled
        assert istio_config.version == '1.19.0'

        # Cleanup
        os.unlink(temp_config_path)

        # Test default configuration by not passing a config file
        # and ensuring no config file exists in the current directory
        if os.path.exists("enterprise-sim.yaml"):
            os.rename("enterprise-sim.yaml", "enterprise-sim.yaml.bak")

        config_manager_default = ConfigManager(config_file=None)
        cluster_config_default = config_manager_default.get_cluster_config()

        assert cluster_config_default.name == "enterprise-sim"
        assert cluster_config_default.workers == 3
        # assert cluster_config_default.registry_port == 5000

        if os.path.exists("enterprise-sim.yaml.bak"):
            os.rename("enterprise-sim.yaml.bak", "enterprise-sim.yaml")

        print("✅ ConfigManager tests passed")
        return True

    except Exception as e:
        import traceback
        traceback.print_exc()
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
        # Mock the kubernetes library to avoid actual cluster connection
        sys.modules['kubernetes'] = MagicMock()

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
    finally:
        # Clean up mocks
        if 'kubernetes' in sys.modules:
            del sys.modules['kubernetes']


def test_k8s_apply_manifest_falls_back_to_kubectl():
    """Ensure apply_manifest falls back to kubectl when client lacks CRDs."""
    from enterprise_sim.utils.k8s import KubernetesClient, utils

    with patch.object(KubernetesClient, '_init_client', return_value=None):
        client = KubernetesClient()
        client.api_client = object()

    manifest = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: test"

    with patch.object(utils, 'create_from_yaml', side_effect=AttributeError('missing api')):
        with patch('enterprise_sim.utils.k8s.subprocess.run') as mock_run:
            mock_process = MagicMock(returncode=0, stdout='', stderr='')
            mock_run.return_value = mock_process

            assert client.apply_manifest(manifest, 'test-ns')

            assert mock_run.called
            cmd_used = mock_run.call_args[0][0]
            assert cmd_used[0] == 'kubectl'
            assert '-f' in cmd_used
            kwargs = mock_run.call_args[1]
            assert kwargs.get('input') == manifest

    print("✅ apply_manifest falls back to kubectl when API lacks CRDs")
    return True


def test_config_manager_dev_domain_allows_missing_cloudflare():
    """Ensure dev-like domains skip Cloudflare credential requirement."""
    from enterprise_sim.core.config import ConfigManager

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({
            'environment': {
                'domain': 'local.devcluster.example'
            }
        }, f)
        temp_config_path = f.name

    try:
        with patch.object(ConfigManager, '_command_exists', return_value=True):
            with patch.dict(os.environ, {
                'CLOUDFLARE_EMAIL': '',
                'CLOUDFLARE_API_TOKEN': ''
            }, clear=False):
                config_manager = ConfigManager(config_file=temp_config_path)
                # Should not raise even though credentials are empty
                config_manager.validate_config()
                print("✅ Dev domain allows missing Cloudflare credentials")
                return True
    finally:
        os.unlink(temp_config_path)


def test_config_manager_prod_domain_requires_cloudflare():
    """Ensure production domains still require Cloudflare credentials."""
    from enterprise_sim.core.config import ConfigManager

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({
            'environment': {
                'domain': 'prod.butterflycluster.com'
            }
        }, f)
        temp_config_path = f.name

    try:
        with patch.object(ConfigManager, '_command_exists', return_value=True):
            with patch.dict(os.environ, {
                'CLOUDFLARE_EMAIL': '',
                'CLOUDFLARE_API_TOKEN': ''
            }, clear=False):
                config_manager = ConfigManager(config_file=temp_config_path)
                try:
                    config_manager.validate_config()
                except EnvironmentError as exc:
                    assert 'CLOUDFLARE_API_TOKEN' in str(exc)
                    print("✅ Prod domain enforces Cloudflare credentials")
                    return True
                else:
                    raise AssertionError('Expected EnvironmentError for missing Cloudflare credentials')
    finally:
        os.unlink(temp_config_path)


def test_cli():
    """Test CLI functionality."""
    try:
        # Mock the kubernetes library to avoid actual cluster connection
        sys.modules['kubernetes'] = MagicMock()

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
        assert args.force

        print("✅ CLI tests passed")
        return True

    except Exception as e:
        print(f"❌ CLI test failed: {e}")
        return False
    finally:
        # Clean up mocks
        if 'kubernetes' in sys.modules:
            del sys.modules['kubernetes']


def main():
    """Run all tests."""
    print("Running Phase 1 Implementation Tests...")
    print("=" * 50)

    tests = [
        test_imports,
        test_config_manager,
        test_cluster_manager,
        test_k8s_client,
        test_k8s_apply_manifest_falls_back_to_kubectl,
        test_config_manager_dev_domain_allows_missing_cloudflare,
        test_config_manager_prod_domain_requires_cloudflare,
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
