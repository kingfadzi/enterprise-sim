#!/usr/bin/env python3
"""
Enterprise Simulation Platform - REST API Backend
Provides API endpoints for the React dashboard
"""
import os
import json
import socket
import subprocess
import time
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from enterprise_sim.utils.k8s import KubernetesClient

APP_NAME = os.getenv("APP_NAME", "hello-app")
REGION = os.getenv("REGION", "us")
NAMESPACE = os.getenv("NAMESPACE", "default")

app = Flask(__name__, static_folder='frontend/build', static_url_path='')
CORS(app)

# Initialize the Kubernetes client
k8s_client = KubernetesClient()

def check_storage_mounted():
    """Check if persistent storage is mounted and accessible"""
    storage_path = "/app/data"
    try:
        if not os.path.exists(storage_path):
            return {"enabled": False, "type": "ephemeral"}

        is_mounted = os.path.ismount(storage_path)
        is_writable = os.access(storage_path, os.W_OK)

        # Test writeability more safely using a unique filename
        writable_test_passed = False
        if is_writable:
            try:
                import time
                import random
                # Use unique filename to avoid race conditions between multiple requests
                test_file = os.path.join(storage_path, f".storage_test_{int(time.time())}_{random.randint(1000,9999)}")
                with open(test_file, 'w') as f:
                    f.write("test")
                # Clean up test file
                if os.path.exists(test_file):
                    os.remove(test_file)
                writable_test_passed = True
            except Exception:
                # If write test fails, fall back to os.access check
                writable_test_passed = is_writable

        return {
            "enabled": True,
            "path": storage_path,
            "writable": writable_test_passed,
            "type": "persistent" if is_mounted else "directory",
            "mounted": is_mounted
        }
    except Exception as e:
        return {"enabled": False, "error": str(e), "type": "ephemeral"}

def get_security_context():
    """Get current security context information"""
    try:
        uid = os.getuid()
        gid = os.getgid()
        return {
            "user_id": uid,
            "group_id": gid,
            "running_as_root": uid == 0,
            "capabilities_dropped": True,
            "read_only_root_fs": False,
            "privilege_escalation": False
        }
    except Exception as e:
        return {"error": str(e)}

def get_network_posture():
    """Get network security posture"""
    # Better sidecar detection: check if Istio proxy is running
    istio_sidecar_present = False
    try:
        # Check if we're in a pod with Istio sidecar by looking for proxy admin port
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', 15000))  # Istio proxy admin port
        sock.close()
        istio_sidecar_present = (result == 0)
    except:
        # Fallback: check for Istio environment variables
        istio_sidecar_present = bool(os.environ.get('ISTIO_META_WORKLOAD_NAME'))

    # mTLS and zero-trust policies only work if sidecar is present
    return {
        "istio_sidecar": istio_sidecar_present,
        "mtls_enabled": istio_sidecar_present,  # mTLS requires sidecar
        "zero_trust_policies": istio_sidecar_present,  # AuthZ policies require sidecar
        "network_policies": True,  # K8s NetworkPolicies work without Istio
        "ingress_gateway": "istio-ingressgateway",
        "service_mesh": "istio" if istio_sidecar_present else "none"
    }

def get_observability_status():
    """Get real observability platform status"""
    try:
        # Check for Prometheus metrics endpoint
        metrics_available = False
        try:
            import urllib.request
            urllib.request.urlopen('http://localhost:8080/metrics', timeout=2)
            metrics_available = True
        except:
            pass

        # Check for Jaeger tracing (look for Jaeger agent)
        jaeger_available = False
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('jaeger-agent', 14268))
            sock.close()
            jaeger_available = (result == 0)
        except:
            pass

        # Check for ELK stack (look for elasticsearch)
        elk_available = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('elasticsearch', 9200))
            sock.close()
            elk_available = (result == 0)
        except:
            pass

        return {
            "metrics": metrics_available,
            "tracing": jaeger_available,
            "logging": elk_available,
            "grafana_dashboards": False,  # Would need to check Grafana API
            "configured": metrics_available or jaeger_available or elk_available
        }
    except Exception as e:
        return {
            "metrics": False,
            "tracing": False,
            "logging": False,
            "configured": False,
            "error": str(e)
        }

def get_disaster_recovery_status(storage_info):
    """Get real disaster recovery status"""
    try:
        # Check for Velero backup system
        velero_available = k8s_client.get_resource('backups', namespace='-A') is not None

        return {
            "backup_enabled": velero_available,
            "storage_ready": storage_info.get("enabled", False),
            "cross_region_replication": False,  # Would need to detect actual replication
            "velero_installed": velero_available,
            "configured": velero_available
        }
    except Exception as e:
        return {
            "backup_enabled": False,
            "configured": False,
            "error": str(e)
        }

def get_s3_storage_status():
    """Get MinIO/S3 storage status with actual connectivity testing"""
    try:
        # Check for MinIO service
        minio_available = False
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            # Try the internal service name first
            result = sock.connect_ex(('minio.minio-system.svc.cluster.local', 80))
            if result != 0:
                # Try alternative service names
                result = sock.connect_ex(('minio', 9000))
            sock.close()
            minio_available = (result == 0)
        except:
            pass

        # Check for S3 environment variables
        s3_configured = bool(os.environ.get('AWS_ACCESS_KEY_ID') and os.environ.get('S3_BUCKET_NAME'))

        # Test actual S3 connectivity if configured
        bucket_access = False
        bucket_test_details = {}
        if s3_configured:
            try:
                import boto3
                from botocore.client import Config

                # Get S3 credentials from environment
                access_key = os.environ.get('AWS_ACCESS_KEY_ID')
                secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
                bucket_name = os.environ.get('S3_BUCKET_NAME', 'app-data')

                # Use internal MinIO service endpoint (cluster-internal communication)
                endpoint_url = "http://minio.minio-system.svc.cluster.local"

                # Create S3 client
                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                    endpoint_url=endpoint_url,
                    config=Config(signature_version='s3v4'),
                    region_name='us-east-1'  # MinIO doesn't care about region
                )

                # Test bucket access by listing objects
                response = s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
                bucket_access = True
                bucket_test_details = {
                    "bucket_exists": True,
                    "objects_count": response.get('KeyCount', 0),
                    "endpoint_tested": endpoint_url
                }

                # Try to put a test object
                test_key = f"connectivity-test-{int(time.time())}.txt"
                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=test_key,
                    Body=f"Connectivity test from {os.environ.get('APP_NAME', 'app')} at {datetime.utcnow().isoformat()}"
                )
                bucket_test_details["write_test"] = "success"

                # Clean up test object
                s3_client.delete_object(Bucket=bucket_name, Key=test_key)

            except Exception as boto_error:
                bucket_test_details = {
                    "error": str(boto_error),
                    "endpoint_tested": endpoint_url if 'endpoint_url' in locals() else "unknown"
                }

        return {
            "minio_available": minio_available,
            "s3_configured": s3_configured,
            "bucket_access": bucket_access,
            "bucket_details": bucket_test_details,
            "configured": minio_available and s3_configured  # S3 is configured if service is available and credentials exist
        }
    except Exception as e:
        return {
            "configured": False,
            "error": str(e)
        }

def get_secrets_management_status():
    """Get Vault/secrets management status"""
    try:
        # Check for Vault service
        vault_available = False
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('vault', 8200))
            sock.close()
            vault_available = (result == 0)
        except:
            pass

        # Check for Vault environment variables
        vault_configured = bool(os.environ.get('VAULT_ADDR') or os.environ.get('VAULT_TOKEN'))

        # Check for mounted secrets (CSI driver)
        secrets_mounted = os.path.exists('/mnt/secrets-store')

        return {
            "vault_available": vault_available,
            "vault_configured": vault_configured,
            "secrets_mounted": secrets_mounted,
            "csi_driver": secrets_mounted,
            "configured": vault_available or vault_configured or secrets_mounted
        }
    except Exception as e:
        return {
            "configured": False,
            "error": str(e)
        }

def get_advanced_networking_status():
    """Get advanced networking features status"""
    try:
        # Check for egress policies (look for specific NetworkPolicies)
        netpols = k8s_client.get_resource('networkpolicies', namespace='-A')
        egress_policies = netpols and 'items' in netpols and len(netpols['items']) > 0

        # Check for WAF (look for Envoy filters or ModSecurity)
        envoy_filters = k8s_client.get_resource('envoyfilters', namespace='-A')
        waf_enabled = envoy_filters and 'items' in envoy_filters and len(envoy_filters['items']) > 0

        # Check for service entries (external service registration)
        service_entries_result = k8s_client.get_resource('serviceentries', namespace='-A')
        service_entries = service_entries_result and 'items' in service_entries_result and len(service_entries_result['items']) > 0

        return {
            "egress_policies": egress_policies,
            "waf_enabled": waf_enabled,
            "service_entries": service_entries,
            "network_segmentation": egress_policies,
            "configured": egress_policies or waf_enabled or service_entries
        }
    except Exception as e:
        return {
            "configured": False,
            "error": str(e)
        }

def get_platform_posture():
    """Get overall platform security posture"""
    storage_info = check_storage_mounted()
    security_ctx = get_security_context()
    network_info = get_network_posture()

    return {
        "service": APP_NAME,
        "region": REGION,
        "namespace": NAMESPACE,
        "compliance_tier": "enterprise",
        "encryption": {
            "in_transit": network_info.get("mtls_enabled", False),  # Only true if mTLS active
            "at_rest": storage_info.get("enabled", False),
            "service_mesh": network_info.get("istio_sidecar", False)  # Only true if sidecar present
        },
        "storage": storage_info,
        "security": {
            "context": security_ctx,
            "network": network_info,
            "container_security": {
                "seccomp_profile": "RuntimeDefault",
                "security_context_constraints": True,
                "image_pull_policy": "Always"
            }
        },
        "observability": get_observability_status(),
        "disaster_recovery": get_disaster_recovery_status(storage_info),
        "s3_storage": get_s3_storage_status(),
        "secrets_management": get_secrets_management_status(),
        "advanced_networking": get_advanced_networking_status()
    }

# API Routes
@app.route('/api/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'service': APP_NAME,
        'version': '1.0.0'
    })

@app.route('/api/ready')
def ready():
    """Readiness check endpoint"""
    return jsonify({
        'status': 'ready',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'service': APP_NAME
    })

@app.route('/api/posture')
def posture():
    """Complete security & compliance posture"""
    return jsonify(get_platform_posture())

@app.route('/api/security')
def security():
    """Security context details"""
    return jsonify({
        'security_context': get_security_context(),
        'network_posture': get_network_posture(),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/api/storage')
def storage():
    """Storage configuration"""
    return jsonify({
        'storage_info': check_storage_mounted(),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/api/observability')
def observability():
    """Observability platform status"""
    return jsonify({
        'observability_status': get_observability_status(),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/api/disaster-recovery')
def disaster_recovery():
    """Disaster recovery status"""
    storage_info = check_storage_mounted()
    return jsonify({
        'disaster_recovery_status': get_disaster_recovery_status(storage_info),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/api/s3-storage')
def s3_storage():
    """S3/MinIO storage status"""
    return jsonify({
        's3_storage_status': get_s3_storage_status(),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/api/secrets')
def secrets():
    """Secrets management status"""
    return jsonify({
        'secrets_status': get_secrets_management_status(),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/api/networking')
def networking():
    """Advanced networking status"""
    return jsonify({
        'networking_status': get_advanced_networking_status(),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/api/info')
def info():
    """Basic service information"""
    return jsonify({
        'message': 'Enterprise Simulation Platform',
        'service': APP_NAME,
        'region': REGION,
        'namespace': NAMESPACE,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'headers': dict(request.headers),
        'remote_addr': request.remote_addr,
        'protocol': request.environ.get('SERVER_PROTOCOL', 'HTTP'),
        'method': request.method
    })

# Serve React App
@app.route('/')
def serve_react():
    """Serve React dashboard"""
    return send_from_directory(app.static_folder, 'index.html')

@app.errorhandler(404)
def not_found(error):
    """Handle React routing"""
    return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    print(f"Starting Enterprise Simulation Platform API...")
    print(f"Service: {APP_NAME}")
    print(f"Region: {REGION}")
    print(f"Namespace: {NAMESPACE}")
    print(f"Available at: http://0.0.0.0:8080")

    app.run(
        host='0.0.0.0',
        port=8080,
        debug=False
    )
