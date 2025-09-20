# Enterprise Simulation Platform - Full Reset Process Breakdown

## Overview
This document provides a detailed breakdown of the `./enterprise-sim.sh reset` command, which performs a complete platform rebuild: cluster + Istio + cert-manager + TLS + regions + gateway + storage + MinIO + app image.

## Phase 1: Teardown (`cmd_down`)

### 1.1 Delete k3d Cluster

**System Commands:**
- `k3d cluster delete "$CLUSTER_NAME" || true` - Delete existing k3d cluster

**File Operations:**
- `rm -f "./sample-app/.env"` - Remove generated sample app environment file

**Why:** Clean slate removal of existing infrastructure and configuration files

---

## Phase 2: Full Platform Build (`cmd_full_up`)

### 2.1 Cluster Creation (`cmd_up`)

**System Commands:**
- `k3d cluster list -o json | jq -e ".[] | select(.name==\"$CLUSTER_NAME\")"` - Check if cluster exists
- `k3d kubeconfig write "$CLUSTER_NAME" 2>/dev/null` - Test cluster health by writing kubeconfig
- `k3d cluster delete "$CLUSTER_NAME" || true` - Delete unhealthy cluster if needed
- `k3d cluster create "$CLUSTER_NAME" --agents 1 --port '80:80@loadbalancer' --port '443:443@loadbalancer' --k3s-arg '--disable=traefik@server:0' --wait` - Create new cluster
- `k3d kubeconfig write "$CLUSTER_NAME"` - Generate kubeconfig file

**Kubectl Commands:**
- `KUBECONFIG="$kubeconfig_path" kubectl get nodes >/dev/null 2>&1` - Test cluster connectivity
- `KUBECONFIG="$kubeconfig" kubectl get nodes -o wide` - Verify cluster nodes are ready

**Environment Setup:**
- `generate_sample_app_env` - Generate app environment variables and .env file

**Why:** Creates the base Kubernetes cluster with ports 80/443 exposed and Traefik disabled (we'll use Istio instead)

### 2.2 Istio Installation (`cmd_istio_up`)

**System Commands:**
- `command -v istioctl >/dev/null 2>&1` - Check if istioctl is available
- `istioctl install --set profile=demo -y` - Install Istio with demo profile

**Kubectl Commands:**
- `kubectl -n istio-system rollout status deploy/istiod --timeout=300s` - Wait for control plane
- `kubectl -n istio-system rollout status deploy/istio-ingressgateway --timeout=300s` - Wait for ingress gateway

**Why:** Installs service mesh for traffic management, security policies, and observability

### 2.3 Cert-manager Installation (`cmd_certmgr_up`) - Conditional

**Conditions Check:**
- `[ -n "${CLOUDFLARE_EMAIL:-}" ] && [ -n "${CLOUDFLARE_API_TOKEN:-}" ]` - Check if CloudFlare credentials exist

**Kubectl Commands:**
- `kubectl -n cert-manager get deploy cert-manager >/dev/null 2>&1` - Check if already installed
- `kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.18.2/cert-manager.yaml` - Install cert-manager
- `kubectl -n cert-manager rollout status deploy/cert-manager --timeout=300s` - Wait for cert-manager
- `kubectl -n cert-manager rollout status deploy/cert-manager-webhook --timeout=300s` - Wait for webhook
- `kubectl -n cert-manager rollout status deploy/cert-manager-cainjector --timeout=300s` - Wait for CA injector
- `kubectl patch deployment cert-manager -n cert-manager --type='json' -p='[...]'` - Configure DNS settings
- `kubectl rollout status deployment/cert-manager -n cert-manager --timeout=300s` - Wait for restart

**Why:** Enables automatic Let's Encrypt certificate management with DNS01 validation

### 2.4 TLS Setup (`cmd_tls_up`)

**Kubectl Commands:**
- `kubectl create namespace istio-system >/dev/null 2>&1 || true` - Ensure istio-system namespace exists
- `kubectl -n istio-system get secret "$TLS_SECRET_NAME" >/dev/null 2>&1` - Check existing certificates

**Certificate Validation:**
- `kubectl -n "$ns" get secret "$secret" -o jsonpath='{.data.tls\.crt}'` - Extract certificate data
- `echo "$cert_data" | base64 -d > "$tmp"` - Decode certificate
- `openssl x509 -checkend 604800 -noout -in "$tmp"` - Check if cert valid for 7+ days

**If using cert-manager (has_certmanager_config):**
- `kubectl -n cert-manager get deploy cert-manager >/dev/null 2>&1` - Verify cert-manager installed
- `kubectl create secret generic cloudflare-api-token-secret -n cert-manager --from-literal=api-token="$CLOUDFLARE_API_TOKEN" --dry-run=client -o yaml | kubectl apply -f -` - Create CloudFlare credentials
- `envsubst < "manifests/certmgr/cluster-issuers-template.yaml" | kubectl apply -f -` - Apply ClusterIssuers
- `kubectl wait --for=condition=Ready "clusterissuer/$issuer_name" --timeout=120s` - Wait for issuer
- `envsubst < "manifests/certmgr/wildcard-certificate-template.yaml" | kubectl apply -f -` - Request certificate
- `kubectl -n istio-system get certificate "$cert_name" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'` - Monitor certificate status

**If using self-signed (setup_selfsigned_tls):**
- `openssl req -x509 -nodes -newkey rsa:2048 -days 365 -keyout "$tmpdir/tls.key" -out "$tmpdir/tls.crt" -subj "/CN=*.${K3S_INGRESS_DOMAIN}" -addext "subjectAltName = DNS:*.${K3S_INGRESS_DOMAIN}, DNS:${K3S_INGRESS_DOMAIN}"` - Generate self-signed cert
- `kubectl -n istio-system create secret tls "$TLS_SECRET_NAME" --key "$tmpdir/tls.key" --cert "$tmpdir/tls.crt" --dry-run=client -o yaml | kubectl apply -f -` - Create TLS secret

**Backup Operations:**
- `kubectl -n "$ns" get secret "$secret" -o yaml > "$backup_file"` - Backup certificate

**Why:** Provides HTTPS certificates for secure communication

### 2.5 Region Namespaces (`cmd_regions_up`)

**Kubectl Commands:**
- `kubectl get ns "region-us" >/dev/null 2>&1` - Check if namespace exists
- `kubectl create ns "region-us"` - Create namespace if missing
- `kubectl label ns "region-us" istio-injection=enabled --overwrite` - Enable Istio injection
- `kubectl label ns "region-us" compliance.region="us" --overwrite` - Label for region compliance
- `kubectl get crd peerauthentications.security.istio.io >/dev/null 2>&1` - Check if Istio CRDs exist

**Policy Applications (per region: us, eu, ap):**
```bash
# STRICT mTLS Policy
kubectl apply -f - <<EOF
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: ${ns}
spec:
  mtls:
    mode: STRICT
EOF

# Authorization Policy
kubectl apply -f - <<EOF
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: allow-ingress
  namespace: ${ns}
spec:
  action: ALLOW
  rules:
  - from:
    - source:
        namespaces: ["istio-system"]
EOF

# NetworkPolicy
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: baseline-istio-access
  namespace: ${ns}
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: istio-system
      podSelector:
        matchLabels:
          istio: ingressgateway
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchLabels:
          k8s-app: kube-dns
    ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: istio-system
    ports:
    - protocol: TCP
      port: 15012
EOF
```

**Why:** Creates isolated region namespaces (us, eu, ap) with zero-trust security policies

### 2.6 Gateway Setup (`cmd_gateway_up`)

**Environment Setup:**
- `require_domain` - Ensure K3S_INGRESS_DOMAIN is set
- `derive_env_defaults` - Set GATEWAY_NAME and TLS_SECRET_NAME

**System Commands:**
- `envsubst < "$(dirname "$0")/manifests/gateway/wildcard-gateway-template.yaml" > "$TMPFILE"` - Template gateway config

**Kubectl Commands:**
- `kubectl apply -f "$TMPFILE"` - Apply gateway configuration
- `kubectl -n istio-system get gateway "$GATEWAY_NAME" -o yaml >/dev/null` - Verify gateway creation

**Why:** Creates the wildcard HTTPS gateway that serves as the entry point for all external traffic, enabling `*.domain.com` routing with TLS termination

---

## Phase 3: Storage Platform (`cmd_storage_up`)

### 3.1 OpenEBS Installation

**System Commands:**
- `command -v helm >/dev/null 2>&1` - Check if helm is available
- `helm repo add openebs https://openebs.github.io/charts >/dev/null 2>&1` - Add OpenEBS repository
- `helm repo update >/dev/null 2>&1` - Update helm repositories
- `helm upgrade --install openebs openebs/openebs --namespace openebs-system --create-namespace --set engines.local.lvm.enabled=false --set engines.local.zfs.enabled=false --set engines.replicated.mayastor.enabled=false --set engines.local.hostpath.enabled=true --set localpv-provisioner.hostpathClass.enabled=true --set localpv-provisioner.hostpathClass.name=openebs-hostpath --set localpv-provisioner.hostpathClass.isDefaultClass=false --set ndm.enabled=false --set ndmOperator.enabled=false --wait --timeout=600s` - Install OpenEBS

**Kubectl Commands:**
- `kubectl -n openebs-system wait --for=condition=ready pod --all --timeout=300s` - Wait for OpenEBS components
- `kubectl apply -f "$TMPFILE"` - Apply storage classes (enterprise-standard, enterprise-ssd, enterprise-fast)
- `kubectl get storageclass -l compliance.storage/managed-by=enterprise-sim` - Verify storage classes

**Why:** Provides persistent storage with multiple performance tiers for applications

---

## Phase 4: Object Storage (`cmd_minio_up`)

### 4.1 MinIO Operator Installation

**System Commands:**
- `helm repo add minio-operator https://operator.min.io >/dev/null 2>&1` - Add MinIO repository
- `helm repo update >/dev/null 2>&1` - Update helm repositories
- `helm upgrade --install minio-operator minio-operator/operator --namespace minio-operator --create-namespace --set operator.replicaCount=1 --set console.enabled=true --set console.service.type=ClusterIP --wait --timeout=600s` - Install MinIO Operator

**Kubectl Commands:**
- `kubectl -n minio-operator wait --for=condition=ready pod --all --timeout=300s` - Wait for operator

### 4.2 MinIO Tenant Setup

**Kubectl Commands:**
- `kubectl create namespace "$MINIO_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -` - Create minio-system namespace
- `kubectl label namespace "$MINIO_NAMESPACE" istio-injection=enabled --overwrite` - Enable Istio injection
- `kubectl apply -f "$TMPFILE"` - Apply tenant configuration (credentials, tenant, network policies)

### 4.3 MinIO External Routing

**System Commands:**
- `envsubst` - Template VirtualServices for external access

**Kubectl Commands:**
- `kubectl apply -f "$ROUTE_TMPFILE"` - Apply S3 API routing (s3.domain.com)
- `kubectl apply -f "$ROUTE_TMPFILE"` - Apply Console routing (minio-console.domain.com)

**Why:** Provides S3-compatible object storage with web console for applications

---

## Phase 5: Application Image Build (`build_and_import_app_image`)

### 5.1 Docker Image Build

**System Commands:**
- `docker build -t hello-app:latest "$APP_DIR"` - Build multi-stage Docker image (React frontend + Python backend)

**Environment Check:**
- `[ ! -d "$APP_DIR" ]` - Verify sample-app directory exists

### 5.2 Image Import

**System Commands:**
- `k3d image import hello-app:latest -c "$CLUSTER_NAME"` - Import image into k3d cluster

**Why:** Builds and imports sample application image ready for deployment

---

## Phase 6: Environment Generation (`generate_sample_app_env`)

### 6.1 App Environment Setup

**File Operations:**
- `cat > "$APP_ENV_PATH" <<EOF` - Create sample-app/.env file with APP_NAME and REGION

**Environment Export:**
- `export NAMESPACE APP_NAME REGION` - Export variables for templating
- `export STORAGE_PERSISTENT_ENABLED STORAGE_PERSISTENT_SIZE STORAGE_PERSISTENT_CLASS` - Export storage variables
- `export S3_ENABLED S3_BUCKET_NAME S3_ENDPOINT S3_USE_SSL` - Export S3 variables
- `export K3S_INGRESS_DOMAIN` - Export platform configuration

**Storage Validation:**
- `kubectl get storageclass "${STORAGE_PERSISTENT_CLASS}" >/dev/null 2>&1` - Validate storage class exists (if storage enabled)

**Why:** Prepares application environment variables and validates platform readiness for deployments

---

## Summary

The reset command performs a complete enterprise platform rebuild with:
- **Infrastructure**: k3d cluster with exposed ports
- **Service Mesh**: Istio for traffic management and security
- **Certificate Management**: Automated HTTPS with Let's Encrypt or self-signed
- **Security**: Zero-trust policies with mTLS and network policies
- **Networking**: Wildcard gateway for external access
- **Storage**: OpenEBS with multiple performance tiers
- **Object Storage**: MinIO with S3-compatible API
- **Application Ready**: Built and imported sample app image

Each phase builds upon the previous one, creating a cohesive enterprise-grade platform ready for application deployment.

## Prerequisites

### Required Tools
```bash
command -v k3d || echo "Missing: k3d"
command -v kubectl || echo "Missing: kubectl"
command -v jq || echo "Missing: jq"
command -v istioctl || echo "Missing: istioctl"
command -v helm || echo "Missing: helm"
command -v envsubst || echo "Missing: envsubst"
command -v openssl || echo "Missing: openssl"
command -v docker || echo "Missing: docker"
```

### Environment Configuration
The reset process requires a valid environment configuration file in `config/enterprise-sim.{env}`. Example:

```bash
# Environment determined by single config file in config/ directory
export ENVIRONMENT="dev"
export BASE_DOMAIN="mycompany.com"
export K3S_INGRESS_DOMAIN="dev.mycompany.com"
export CLUSTER_NAME="enterprise-sim-dev"
export TLS_SECRET_NAME="dev-wildcard-tls"
export GATEWAY_NAME="dev-sim-gateway"
export USE_PROD_CERTS="false"

# CloudFlare credentials (optional, for Let's Encrypt)
export CLOUDFLARE_EMAIL="admin@mycompany.com"
export CLOUDFLARE_API_TOKEN="your-cloudflare-token"
export CLOUDFLARE_ZONE_ID="your-zone-id"
```

## Usage

```bash
./enterprise-sim.sh reset
```

This command will:
1. Delete any existing cluster and configuration
2. Build a complete enterprise platform from scratch
3. Prepare the environment for application deployment

After completion, deploy applications with:
```bash
./enterprise-sim.sh app deploy
./enterprise-sim.sh validate
```