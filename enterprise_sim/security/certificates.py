"""TLS certificate lifecycle management."""

import subprocess
import os
import tempfile
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from ..utils.k8s import KubernetesClient
from ..utils.manifests import load_single_manifest, render_manifest
import base64
import yaml
from cryptography import x509
from cryptography.hazmat.backends import default_backend


class CertificateManager:
    """Manages TLS certificates for the enterprise simulation."""

    def __init__(self, k8s_client: KubernetesClient, domain: str = "localhost"):
        self.k8s = k8s_client
        self.domain = domain
        self.wildcard_domain = f"*.{domain}"
        self.secret_name = f"{domain.replace('.', '-')}-tls"

    def setup_certificates(self, mode: str = "self-signed", staging: bool = True) -> bool:
        """Setup TLS certificates for the simulation.

        Args:
            mode: Either 'self-signed' or 'letsencrypt'
            staging: Use Let's Encrypt staging environment (default True to avoid rate limits)
        """
        print(f"Setting up TLS certificates for {self.wildcard_domain}")
        print(f"Certificate mode: {mode}")

        # Check if we can reuse existing certificate (in cluster or from backup)
        if self._cert_is_valid_in_cluster():
            print(f"Reusing valid certificate from cluster: {self.secret_name}")
            self._backup_certificate()
            return True

        # If not in cluster, try to restore from backup
        if self._cert_is_valid_from_backup():
            if self._restore_certificate_from_backup():
                print(f"Reusing valid certificate from backup: {self.secret_name}")
                return True

        # Validate domain for Let's Encrypt
        if mode == "letsencrypt":
            if not self._validate_domain_for_letsencrypt():
                print("ERROR: Domain is not suitable for Let's Encrypt")
                print("       For localhost/development, use self-signed certificates:")
                print("       enterprise-sim security setup-certificates --mode self-signed")
                return False

        # Create new certificate
        print(f"Creating new certificate: {self.secret_name}")
        if mode == "self-signed":
            return self._create_self_signed_certificate()
        elif mode == "letsencrypt":
            return self._setup_letsencrypt_certificate(staging)
        else:
            print(f"ERROR: Unsupported certificate mode: {mode}")
            return False

    def _create_self_signed_certificate(self) -> bool:
        """Create self-signed wildcard certificate."""
        print("Creating self-signed wildcard certificate...")

        try:
            # Create temporary directory for certificate generation
            with tempfile.TemporaryDirectory() as temp_dir:
                key_file = os.path.join(temp_dir, "tls.key")
                cert_file = os.path.join(temp_dir, "tls.crt")
                config_file = os.path.join(temp_dir, "openssl.conf")

                # Create OpenSSL configuration
                self._create_openssl_config(config_file)

                # Generate private key
                print("  Generating private key...")
                subprocess.run([
                    'openssl', 'genrsa', '-out', key_file, '2048'
                ], check=True, capture_output=True)

                # Generate certificate
                print("  Generating certificate...")
                subprocess.run([
                    'openssl', 'req', '-new', '-x509', '-key', key_file,
                    '-out', cert_file, '-days', '365', '-config', config_file,
                    '-extensions', 'v3_req'
                ], check=True, capture_output=True)

                # Read certificate and key
                with open(cert_file, 'r') as f:
                    cert_data = f.read()
                with open(key_file, 'r') as f:
                    key_data = f.read()

                # Create Kubernetes secret
                return self._create_tls_secret(cert_data, key_data)

        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to create self-signed certificate: {e}")
            return False
        except Exception as e:
            print(f"ERROR: Certificate creation failed: {e}")
            return False

    def _create_openssl_config(self, config_file: str):
        """Create OpenSSL configuration for wildcard certificate."""
        config_content = f"""
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = US
ST = CA
L = San Francisco
O = Enterprise Simulation
OU = IT Department
CN = {self.domain}

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = {self.domain}
DNS.2 = {self.wildcard_domain}
"""
        with open(config_file, 'w') as f:
            f.write(config_content)

    def _setup_letsencrypt_certificate(self, staging: bool = True) -> bool:
        """Setup Let's Encrypt certificate via cert-manager."""
        print("Setting up Let's Encrypt certificate via cert-manager...")

        # Check if cert-manager is available
        if not self._is_cert_manager_available():
            print("ERROR: cert-manager is not available")
            print("       Install cert-manager first: enterprise-sim service install cert-manager")
            return False

        # Check if we have CloudFlare credentials
        if not self._has_cloudflare_credentials():
            print("WARNING: No CloudFlare credentials found")
            print("         Setting environment variables: CLOUDFLARE_EMAIL, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ZONE_ID")
            print("         Falling back to self-signed certificates")
            return self._create_self_signed_certificate()

        # Create CloudFlare secret for cert-manager
        if not self._create_cloudflare_secret():
            print("ERROR: Failed to create CloudFlare credentials secret")
            return False

        # Create ClusterIssuer with CloudFlare DNS-01
        env_name = "staging" if staging else "prod"
        issuer_name = f"letsencrypt-{env_name}"

        if not self._create_cloudflare_cluster_issuer(staging):
            print("ERROR: Failed to create ClusterIssuer")
            return False

        # Wait for ClusterIssuer to be ready
        if not self._wait_for_cluster_issuer(issuer_name):
            print("ERROR: ClusterIssuer not ready")
            return False

        # Create Certificate resource using ClusterIssuer
        return self._create_cloudflare_certificate(staging)

    def _is_cert_manager_available(self) -> bool:
        """Check if cert-manager is installed and ready."""
        try:
            summary = self.k8s.summarize_deployment_readiness('cert-manager', 'cert-manager')
            if not summary:
                return False

            desired = summary['desired_replicas'] or summary['effective_total']
            ready = summary['effective_ready']

            return bool(desired) and ready >= desired

        except Exception:
            return False

    def _create_letsencrypt_issuer(self, staging: bool = True) -> bool:
        """Create ClusterIssuer for Let's Encrypt."""
        # Check for Cloudflare credentials
        cloudflare_token = os.getenv('CLOUDFLARE_API_TOKEN')
        if not cloudflare_token:
            print("ERROR: CLOUDFLARE_API_TOKEN environment variable not set")
            print("       Set your Cloudflare API token for DNS-01 challenge")
            return False

        # Create secret for Cloudflare token
        secret_manifest = render_manifest(
            "manifests/certmgr/cloudflare-secret.yaml",
            api_token=cloudflare_token,
        )

        if not self.k8s.apply_manifest(secret_manifest, 'cert-manager'):
            print("ERROR: Failed to create Cloudflare token secret")
            return False

        # Create ClusterIssuer
        env_name = "staging" if staging else "prod"
        server_url = "https://acme-staging-v02.api.letsencrypt.org/directory" if staging else "https://acme-v02.api.letsencrypt.org/directory"

        print(f"Creating ClusterIssuer for Let's Encrypt {env_name} environment")
        print(f"Server: {server_url}")

        issuer_manifest = render_manifest(
            "manifests/certmgr/cluster-issuer.yaml",
            issuer_name=f"letsencrypt-{env_name}",
            server_url=server_url,
            email=f"admin@{self.domain}",
            cloudflare_email=os.getenv('CLOUDFLARE_EMAIL', ''),
        )

        if not self.k8s.apply_manifest(issuer_manifest):
            print("ERROR: Failed to create ClusterIssuer")
            return False

        print("ClusterIssuer created successfully")
        return True

    def _create_letsencrypt_certificate(self, staging: bool = True) -> bool:
        """Create Certificate resource for Let's Encrypt."""
        env_name = "staging" if staging else "prod"

        # Ensure target namespace exists before applying
        self.k8s.create_namespace('istio-system')

        certificate_manifest = render_manifest(
            "manifests/certmgr/certificate.yaml",
            certificate_name=self.secret_name,
            issuer_name=f"letsencrypt-{env_name}",
            domain=self.domain,
        )

        # Validate YAML before applying
        if not self._validate_yaml(certificate_manifest):
            print("ERROR: Certificate manifest has invalid YAML syntax")
            return False

        if not self.k8s.apply_manifest(certificate_manifest, 'istio-system'):
            print("ERROR: Failed to create Certificate resource")
            return False

        print("Certificate resource created")
        print("Waiting for certificate to be issued...")

        # Wait for certificate to be ready
        return self._wait_for_certificate_ready(timeout=300)

    def _wait_for_certificate_ready(self, timeout: int = 300) -> bool:
        """Wait for certificate to be ready."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                cert = self.k8s.get_resource('certificate', self.secret_name, 'istio-system')
                if cert:
                    status = cert.get('status', {})
                    conditions = status.get('conditions', [])

                    # Check for Ready condition
                    for condition in conditions:
                        if condition.get('type') == 'Ready':
                            if condition.get('status') == 'True':
                                print("Certificate issued successfully")
                                return True
                            else:
                                # Show why it's not ready
                                reason = condition.get('reason', 'Unknown')
                                message = condition.get('message', 'No message')
                                print(f"Certificate not ready - {reason}: {message}")

                    # Show current status for debugging
                    print(f"Certificate status after {int(time.time() - start_time)}s:")
                    for condition in conditions:
                        cond_type = condition.get('type')
                        cond_status = condition.get('status')
                        cond_reason = condition.get('reason', '')
                        print(f"  {cond_type}: {cond_status} ({cond_reason})")

                    # Check for specific error conditions
                    for condition in conditions:
                        if condition.get('type') == 'Issuing' and condition.get('status') == 'False':
                            reason = condition.get('reason', 'Unknown')
                            message = condition.get('message', '')
                            print(f"ERROR: Certificate issuance failed - {reason}: {message}")
                            return False

                else:
                    print(f"Certificate resource not found after {int(time.time() - start_time)}s")

                time.sleep(10)

            except Exception as e:
                print(f"Error checking certificate status: {e}")
                time.sleep(10)

        print(f"Timeout waiting for certificate (waited {timeout}s)")
        print("Checking final certificate status...")

        # Final status check
        try:
            cert = self.k8s.get_resource('certificate', self.secret_name, 'istio-system')
            if cert:
                status = cert.get('status', {})
                print("Final certificate status:")
                print(f"  Conditions: {status.get('conditions', [])}")

                # Check for CertificateRequest
                cert_requests = self.k8s.get_resource('certificaterequests', namespace='istio-system')
                if cert_requests:
                    print("Related CertificateRequests:")
                    for cr in cert_requests.get('items', []):
                        cr_name = cr.get('metadata', {}).get('name', '')
                        if self.secret_name in cr_name:
                            cr_status = cr.get('status', {})
                            print(f"  {cr_name}: {cr_status}")

        except Exception as e:
            print(f"Error in final status check: {e}")

        return False

    def _create_tls_secret(self, cert_data: str, key_data: str) -> bool:
        """Create TLS secret in istio-system namespace."""
        print(f"Creating TLS secret: {self.secret_name}")

        # Create the secret manifest
        cert_b64 = base64.b64encode(cert_data.encode()).decode()
        key_b64 = base64.b64encode(key_data.encode()).decode()

        secret_manifest = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {self.secret_name}
  namespace: istio-system
type: kubernetes.io/tls
data:
  tls.crt: {cert_b64}
  tls.key: {key_b64}
"""

        # Ensure istio-system namespace exists
        self.k8s.ensure_namespace('istio-system')

        if self.k8s.apply_manifest(secret_manifest, 'istio-system'):
            print(f"TLS secret created: {self.secret_name}")
            # Backup the certificate
            self._backup_certificate()
            return True
        else:
            print("ERROR: Failed to create TLS secret")
            return False

    def get_certificate_info(self) -> Optional[Dict]:
        """Get information about the current certificate."""
        try:
            secret = self.k8s.get_resource('secret', self.secret_name, 'istio-system')
            if not secret:
                return None

            # Extract certificate info
            cert_data = secret.get('data', {}).get('tls.crt')
            if not cert_data:
                return None

            # Decode and parse certificate
            cert_pem = base64.b64decode(cert_data).decode()
            cert = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())

            info = {
                'subject': cert.subject.rfc4514_string(),
                'issuer': cert.issuer.rfc4514_string(),
                'not_before': cert.not_valid_before.isoformat(),
                'not_after': cert.not_valid_after.isoformat(),
                'secret_name': self.secret_name,
                'namespace': 'istio-system',
                'san': [name.value for name in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value]
            }
            return info

        except Exception as e:
            print(f"Error getting certificate info: {e}")
            return None

    def _parse_certificate_info(self, cert_text: str) -> Dict:
        """Parse certificate information from openssl output."""
        info = {}
        lines = cert_text.split('\n')

        for i, line in enumerate(lines):
            if 'Subject:' in line:
                info['subject'] = line.split('Subject:')[1].strip()
            elif 'Issuer:' in line:
                info['issuer'] = line.split('Issuer:')[1].strip()
            elif 'Not Before:' in line:
                info['not_before'] = line.split('Not Before:')[1].strip()
            elif 'Not After :' in line:
                info['not_after'] = line.split('Not After :')[1].strip()
            elif 'DNS:' in line:
                if 'san' not in info:
                    info['san'] = []
                # Extract DNS names
                dns_names = [name.strip() for name in line.replace('DNS:', '').split(',') if name.strip()]
                info['san'].extend(dns_names)

        return info

    def validate_certificate(self) -> bool:
        """Validate the current certificate."""
        print(f"Validating certificate: {self.secret_name}")

        info = self.get_certificate_info()
        if not info:
            print("ERROR: No certificate found")
            return False

        print(f"Certificate subject: {info.get('subject', 'Unknown')}")
        print(f"Certificate issuer: {info.get('issuer', 'Unknown')}")
        print(f"Valid from: {info.get('not_before', 'Unknown')}")
        print(f"Valid until: {info.get('not_after', 'Unknown')}")

        if 'san' in info:
            print(f"Subject Alternative Names: {', '.join(info['san'])}")

        # Check if certificate is valid for our domain
        san_list = info.get('san', [])
        domain_valid = self.domain in san_list or self.wildcard_domain in san_list

        if domain_valid:
            print("Certificate is valid for the configured domain")
            return True
        else:
            print(f"ERROR: Certificate is not valid for domain: {self.domain}")
            return False

    def cleanup_certificates(self) -> bool:
        """Remove TLS certificates and secrets."""
        print("Cleaning up TLS certificates...")

        try:
            # Delete TLS secret
            if self.k8s.get_resource('secret', self.secret_name, 'istio-system'):
                secret_manifest = render_manifest(
                    "manifests/certmgr/tls-secret-delete.yaml",
                    secret_name=self.secret_name,
                )
                self.k8s.delete_manifest(secret_manifest, 'istio-system')
                print(f"TLS secret deleted: {self.secret_name}")

            # Delete Certificate resource if using Let's Encrypt
            if self.k8s.get_resource('certificate', self.secret_name, 'istio-system'):
                cert_manifest = render_manifest(
                    "manifests/certmgr/certificate-delete.yaml",
                    certificate_name=self.secret_name,
                )
                self.k8s.delete_manifest(cert_manifest, 'istio-system')
                print(f"Certificate resource deleted: {self.secret_name}")

            return True

        except Exception as e:
            print(f"ERROR: Failed to cleanup certificates: {e}")
            return False

    def _has_cloudflare_credentials(self) -> bool:
        """Check if CloudFlare credentials are available."""
        return all([
            os.getenv('CLOUDFLARE_EMAIL'),
            os.getenv('CLOUDFLARE_API_TOKEN'),
            os.getenv('CLOUDFLARE_ZONE_ID')
        ])

    def _create_cloudflare_secret(self) -> bool:
        """Create CloudFlare credentials secret for cert-manager."""
        print("Creating CloudFlare credentials secret...")

        secret_manifest = render_manifest(
            "manifests/certmgr/cloudflare-secret.yaml",
            api_token=os.getenv('CLOUDFLARE_API_TOKEN', ''),
        )
        return self.k8s.apply_manifest(secret_manifest, 'cert-manager')

    def _create_cloudflare_cluster_issuer(self, staging: bool = True) -> bool:
        """Create ClusterIssuer with CloudFlare DNS-01 solver."""
        env_name = "staging" if staging else "prod"
        server_url = "https://acme-staging-v02.api.letsencrypt.org/directory" if staging else "https://acme-v02.api.letsencrypt.org/directory"

        print(f"Creating ClusterIssuer: letsencrypt-{env_name}")

        issuer_manifest = render_manifest(
            "manifests/certmgr/cluster-issuer.yaml",
            issuer_name=f"letsencrypt-{env_name}",
            server_url=server_url,
            email=os.getenv('CLOUDFLARE_EMAIL', ''),
            cloudflare_email=os.getenv('CLOUDFLARE_EMAIL', ''),
        )
        return self.k8s.apply_manifest(issuer_manifest)

    def _wait_for_cluster_issuer(self, issuer_name: str, timeout: int = 120) -> bool:
        """Wait for ClusterIssuer to be ready."""
        print(f"Waiting for ClusterIssuer {issuer_name} to be ready...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                issuer = self.k8s.get_resource('clusterissuer', issuer_name)
                if issuer and issuer.get('status', {}).get('conditions'):
                    for condition in issuer['status']['conditions']:
                        if condition.get('type') == 'Ready' and condition.get('status') == 'True':
                            print(f"ClusterIssuer {issuer_name} is ready")
                            return True

                time.sleep(5)
            except Exception as e:
                print(f"Checking ClusterIssuer status: {e}")
                time.sleep(5)

        print(f"ERROR: ClusterIssuer {issuer_name} not ready after {timeout}s")
        return False

    def _create_cloudflare_certificate(self, staging: bool = True) -> bool:
        """Create Certificate resource using CloudFlare DNS-01 ClusterIssuer."""
        env_name = "staging" if staging else "prod"
        issuer_name = f"letsencrypt-{env_name}"
        cert_name = f"{self.domain.replace('.', '-')}-wildcard-cert"

        print(f"Creating Certificate resource: {cert_name}")

        # Ensure target namespace exists before applying
        self.k8s.create_namespace('istio-system')

        certificate_manifest = f"""
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: {cert_name}
  namespace: istio-system
spec:
  secretName: {self.secret_name}
  issuerRef:
    name: {issuer_name}
    kind: ClusterIssuer
  dnsNames:
  - {self.domain}
  - "*.{self.domain}"
"""
        if not self.k8s.apply_manifest(certificate_manifest, 'istio-system'):
            print("ERROR: Failed to create Certificate resource")
            return False

        # Wait for certificate to be ready
        if self._wait_for_certificate(cert_name):
            # Backup the certificate after successful creation
            self._backup_certificate()
            return True
        return False

    def _wait_for_certificate(self, cert_name: str, timeout: int = 900) -> bool:
        """Wait for Certificate to be ready."""
        print(f"Waiting for Certificate {cert_name} to be ready (timeout: {timeout}s)...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                cert = self.k8s.get_resource('certificate', cert_name, 'istio-system')
                if cert and cert.get('status', {}).get('conditions'):
                    for condition in cert['status']['conditions']:
                        if condition.get('type') == 'Ready' and condition.get('status') == 'True':
                            print(f"Certificate {cert_name} is ready")
                            return True

                # Show progress every 30 seconds
                elapsed = int(time.time() - start_time)
                if elapsed % 30 == 0 and elapsed > 0:
                    print(f"Still waiting for certificate... ({elapsed}s elapsed)")

                time.sleep(10)
            except Exception as e:
                print(f"Checking certificate status: {e}")
                time.sleep(10)

        print(f"ERROR: Certificate {cert_name} not ready after {timeout}s")
        return False

    def _backup_certificate(self) -> bool:
        """Backup certificate secret to cluster-state directory."""
        backup_dir = "./cluster-state"
        backup_file = f"{backup_dir}/{self.secret_name}.yaml"

        try:
            os.makedirs(backup_dir, exist_ok=True)
            secret = self.k8s.get_resource('secret', self.secret_name, 'istio-system')
            if not secret:
                print(f"WARNING: Secret {self.secret_name} not found for backup")
                return False

            with open(backup_file, 'w') as f:
                yaml.dump(secret, f)

            print(f"Certificate backed up: {backup_file}")
            return True
        except Exception as e:
            print(f"ERROR: Certificate backup failed: {e}")
            return False

    def _cert_is_valid_in_cluster(self) -> bool:
        """Check if certificate exists in cluster and is valid for at least 7 days."""
        try:
            secret = self.k8s.get_resource('secret', self.secret_name, 'istio-system')
            if not secret:
                return False

            cert_data = secret.get('data', {}).get('tls.crt')
            if not cert_data:
                return False

            cert_pem = base64.b64decode(cert_data).decode()
            cert = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())

            return cert.not_valid_after > datetime.now() + timedelta(days=7)
        except Exception as e:
            print(f"Error checking certificate validity: {e}")
            return False

    def _cert_is_valid_from_backup(self) -> bool:
        """Check if certificate backup exists and is valid."""
        backup_dir = "./cluster-state"
        backup_file = f"{backup_dir}/{self.secret_name}.yaml"

        try:
            if not os.path.exists(backup_file):
                return False

            with open(backup_file, 'r') as f:
                backup_data = yaml.safe_load(f)

            cert_data = backup_data.get('data', {}).get('tls.crt')
            if not cert_data:
                return False

            cert_pem = base64.b64decode(cert_data).decode()
            cert = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())

            return cert.not_valid_after > datetime.now() + timedelta(days=7)
        except Exception as e:
            print(f"Error checking backup certificate validity: {e}")
            return False

    def _restore_certificate_from_backup(self) -> bool:
        """Restore certificate from backup file."""
        backup_dir = "./cluster-state"
        backup_file = f"{backup_dir}/{self.secret_name}.yaml"

        try:
            if not os.path.exists(backup_file):
                print(f"No backup file found: {backup_file}")
                return False

            return self.k8s.apply_file(backup_file)
        except Exception as e:
            print(f"ERROR: Certificate restore failed: {e}")
            return False

    def _validate_domain_for_letsencrypt(self) -> bool:
        """Validate that domain is suitable for Let's Encrypt certificates."""
        # Check for localhost variants
        if self.domain.lower() in ['localhost', '127.0.0.1', '::1']:
            print(f"ERROR: '{self.domain}' is not a valid public domain")
            print("       Let's Encrypt requires a publicly resolvable domain name")
            return False

        # Check for internal/private domains
        if self.domain.endswith('.local') or self.domain.endswith('.internal'):
            print(f"ERROR: '{self.domain}' appears to be an internal domain")
            print("       Let's Encrypt requires a publicly resolvable domain name")
            return False

        # Basic domain validation - must have at least one dot
        if '.' not in self.domain:
            print(f"ERROR: '{self.domain}' is not a valid domain name")
            print("       Domain must have at least one dot (e.g., example.com)")
            return False

        # Must have a valid TLD
        parts = self.domain.split('.')
        if len(parts) < 2 or len(parts[-1]) < 2:
            print(f"ERROR: '{self.domain}' does not have a valid top-level domain")
            print("       Domain must end with a valid public suffix (e.g., .com, .org)")
            return False

        return True

    def _validate_yaml(self, yaml_content: str) -> bool:
        """Validate YAML syntax."""
        try:
            yaml.safe_load(yaml_content)
            return True
        except yaml.YAMLError as e:
            print(f"YAML validation error: {e}")
            return False
        except Exception as e:
            print(f"YAML validation failed: {e}")
            return False
