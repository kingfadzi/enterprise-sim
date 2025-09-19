#!/usr/bin/env bash
set -euo pipefail

# Enterprise Simulation CLI
# Milestones covered: cluster lifecycle, TLS (self-signed), Istio install, regions, zero-trust policies, wildcard gateway.

# Configuration
CLUSTER_NAME=${CLUSTER_NAME:-enterprise-sim}
# BASE_DOMAIN is now set in environment config file

log() { echo ">>> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing dependency: $1"
}

check_deps_basic() { need k3d; need kubectl; need jq; }

check_deps_helm() { need helm; }

need_envsubst() { command -v envsubst >/dev/null 2>&1 || fail "Missing dependency: envsubst"; }

# --- Environment Detection and Config Loading ---

detect_environment() {
  # Find existing config files (excluding template)
  local config_files=(config/enterprise-sim.* )
  local config_count=0
  local found_env=""

  for config_file in "${config_files[@]}"; do
    if [[ -f "$config_file" && "$config_file" != "config/enterprise-sim.template" ]]; then
      config_count=$((config_count + 1))
      found_env=$(basename "$config_file" | sed 's/enterprise-sim\.//')
    fi
  done

  if [ $config_count -eq 0 ]; then
    echo "ERROR: No environment config found. Copy config/enterprise-sim.template to config/enterprise-sim.{env}" >&2
    echo "Example: cp config/enterprise-sim.template config/enterprise-sim.dev" >&2
    exit 1
  elif [ $config_count -gt 1 ]; then
    echo "ERROR: Multiple config files found. Only one environment config should exist:" >&2
    ls -1 config/enterprise-sim.* | grep -v template >&2
    echo "Remove extra configs to prevent accidental deployments." >&2
    exit 1
  fi

  echo "$found_env"
}

load_environment_config() {
  local env="${DETECTED_ENV:-$(detect_environment)}"
  local config_file="config/enterprise-sim.${env}"

  if [ -f "$config_file" ]; then
    log "Loading configuration: $config_file"

    # Set ENVIRONMENT from the config filename (single source of truth)
    export ENVIRONMENT="$env"

    # Export BASE_DOMAIN before sourcing config so it can be used in variable expansion
    export BASE_DOMAIN
    source "$config_file"
    export DETECTED_ENV="$env"

    # Auto-derive K3S_INGRESS_DOMAIN from ENVIRONMENT and BASE_DOMAIN
    if [ -n "${ENVIRONMENT:-}" ] && [ -n "${BASE_DOMAIN:-}" ]; then
      export K3S_INGRESS_DOMAIN="${ENVIRONMENT}.${BASE_DOMAIN}"
    fi

    # Auto-detect USE_PROD_CERTS based on environment (prod = true, others = false)
    export USE_PROD_CERTS=$([ "${ENVIRONMENT:-}" = "prod" ] && echo "true" || echo "false")
  else
    log "No config file found for environment: $env (expected: $config_file)"
    export DETECTED_ENV="$env"
    export ENVIRONMENT="$env"
  fi
}

# --- Certificate Lifecycle Management ---

cert_is_valid() {
  local ns="$1" secret="$2"
  kubectl -n "$ns" get secret "$secret" >/dev/null 2>&1 || return 1

  local cert_data
  cert_data=$(kubectl -n "$ns" get secret "$secret" -o jsonpath='{.data.tls\.crt}' 2>/dev/null) || return 1

  local tmp=$(mktemp)
  echo "$cert_data" | base64 -d > "$tmp"

  # Check if cert is valid for at least 7 days
  if openssl x509 -checkend 604800 -noout -in "$tmp" >/dev/null 2>&1; then
    rm -f "$tmp"
    return 0
  else
    rm -f "$tmp"
    return 1
  fi
}

backup_certificate() {
  local ns="$1" secret="$2"
  local backup_dir="./cluster-state"
  local backup_file="$backup_dir/${secret}.yaml"

  mkdir -p "$backup_dir"

  if kubectl -n "$ns" get secret "$secret" >/dev/null 2>&1; then
    kubectl -n "$ns" get secret "$secret" -o yaml > "$backup_file"
    log "Certificate backed up: $secret"
  fi
}

restore_certificate() {
  local ns="$1" secret="$2"
  local backup_dir="./cluster-state"
  local backup_file="$backup_dir/${secret}.yaml"

  if [ -f "$backup_file" ]; then
    # Ensure namespace exists
    kubectl create namespace "$ns" >/dev/null 2>&1 || true

    # Restore certificate secret
    kubectl apply -f "$backup_file" >/dev/null 2>&1

    # Verify restoration was successful
    if kubectl -n "$ns" get secret "$secret" >/dev/null 2>&1; then
      log "Certificate restored from backup: $secret"
      return 0
    fi
  fi

  return 1
}

cert_is_valid_from_backup() {
  local ns="$1" secret="$2"
  local backup_dir="./cluster-state"
  local backup_file="$backup_dir/${secret}.yaml"

  [ -f "$backup_file" ] || return 1

  # Extract certificate data from backup file
  local cert_data
  cert_data=$(grep 'tls.crt:' "$backup_file" | cut -d' ' -f4) || return 1

  local tmp=$(mktemp)
  echo "$cert_data" | base64 -d > "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }

  # Check if cert is valid for at least 7 days
  if openssl x509 -checkend 604800 -noout -in "$tmp" >/dev/null 2>&1; then
    rm -f "$tmp"
    return 0
  else
    rm -f "$tmp"
    return 1
  fi
}

wait_for_certificate() {
  local ns="$1" cert_name="$2" timeout="${3:-300}"

  log "Waiting for certificate $cert_name to be ready (timeout ${timeout}s)..."

  local start=$(date +%s)
  local check_count=0

  while true; do
    check_count=$((check_count + 1))
    local now=$(date +%s)
    local elapsed=$((now - start))

    # Check if certificate is ready
    if kubectl -n "$ns" get certificate "$cert_name" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q "True"; then
      log "Certificate $cert_name is ready"
      return 0
    fi

    # Show status every 30 seconds or on first check
    if [ $((check_count % 6)) -eq 1 ]; then
      echo ">>> Certificate status (${elapsed}s elapsed):"

      # Show certificate conditions (with connectivity check)
      local conditions
      if kubectl cluster-info >/dev/null 2>&1; then
        conditions=$(kubectl -n "$ns" get certificate "$cert_name" -o jsonpath='{.status.conditions[*].type}:{.status.conditions[*].status}:{.status.conditions[*].message}' 2>/dev/null || echo "")
        if [ -n "$conditions" ]; then
          echo "    Conditions: $conditions"
        else
          echo "    Conditions: (certificate not found or no status yet)"
        fi
      else
        echo "    Conditions: (cluster connectivity issue - retrying...)"
      fi

      # Show any recent events
      echo "    Recent events:"
      kubectl get events -n "$ns" --field-selector involvedObject.name="$cert_name" --sort-by='.lastTimestamp' --no-headers 2>/dev/null | tail -3 | sed 's/^/      /' || echo "      (no events found)"
      echo
    fi

    # Check timeout
    if [ "$elapsed" -ge "$timeout" ]; then
      echo ">>> Certificate creation failed. Full details:"
      kubectl -n "$ns" describe certificate "$cert_name" || true
      echo ">>> Related events:"
      kubectl get events -n "$ns" --field-selector involvedObject.name="$cert_name" --sort-by='.lastTimestamp' || true
      fail "Certificate $cert_name not ready after ${timeout}s"
    fi

    sleep 5
  done
}

apply_manifest_template() {
  local template="$1"
  local output=$(mktemp)

  # Simple variable substitution
  envsubst < "manifests/$template" > "$output"
  kubectl apply -f "$output" || fail "Failed to apply $template"
  rm -f "$output"
}

require_domain() {
  if [ -z "${K3S_INGRESS_DOMAIN:-}" ]; then
    K3S_INGRESS_DOMAIN=localhost
    export K3S_INGRESS_DOMAIN
    echo "WARN: K3S_INGRESS_DOMAIN not set; defaulting to 'localhost' for local testing" >&2
  fi
}

derive_env_defaults() {
  : "${ENVIRONMENT:=}"
  if [ -z "$ENVIRONMENT" ]; then
    case "${K3S_INGRESS_DOMAIN:-}" in
      prod.*|*.prod.*|prod) ENVIRONMENT=prod ;;
      staging.*|*.staging.*|stage|staging) ENVIRONMENT=staging ;;
      dev.*|*.dev.*|dev) ENVIRONMENT=dev ;;
      local.*|*.local.*|local|localhost) ENVIRONMENT=local ;;
      *) ENVIRONMENT=local ;;  # Default to local for safety
    esac
  fi
  : "${TLS_SECRET_NAME:=${ENVIRONMENT}-wildcard-tls}"
  : "${GATEWAY_NAME:=${ENVIRONMENT}-sim-gateway}"
}

cluster_exists() {
  k3d cluster list -o json | jq -e ".[] | select(.name==\"$CLUSTER_NAME\")" >/dev/null 2>&1
}

cluster_is_healthy() {
  # Check if cluster exists first
  cluster_exists || return 1

  # Try to get kubeconfig - if this fails, cluster is broken
  local kubeconfig_path
  kubeconfig_path=$(k3d kubeconfig write "$CLUSTER_NAME" 2>/dev/null) || return 1

  # Test if we can actually connect to the cluster
  KUBECONFIG="$kubeconfig_path" kubectl get nodes >/dev/null 2>&1 || return 1

  return 0
}

generate_sample_app_env() {
  # Load app intent from template
  ENV_PATH="$(dirname "$0")/sample-app/.env.template"
  APP_ENV_PATH="$(dirname "$0")/sample-app/.env"

  # Load app intent (what the app declares about itself)
  if [ -f "$ENV_PATH" ]; then
    set -a
    source "$ENV_PATH"
    set +a
  fi

  # Set app intent defaults
  : "${APP_NAME:=hello-app}"
  : "${REGION:=us}"

  # Platform computes all infrastructure values
  require_domain
  derive_env_defaults
  : "${NAMESPACE:=region-${REGION}}"

  # Validate storage class if storage is enabled
  if [ "${STORAGE_PERSISTENT_ENABLED:-false}" = "true" ]; then
    if ! kubectl get storageclass "${STORAGE_PERSISTENT_CLASS}" >/dev/null 2>&1; then
      fail "Storage class '${STORAGE_PERSISTENT_CLASS}' not found. Install storage platform with: ./enterprise-sim.sh storage up"
    fi
    log "Storage enabled: class=${STORAGE_PERSISTENT_CLASS}, size=${STORAGE_PERSISTENT_SIZE:-1Gi}"
  fi

  # Write app-only .env file (app only sees what it needs)
  cat > "$APP_ENV_PATH" <<EOF
APP_NAME=$APP_NAME
REGION=$REGION
EOF

  # Export only necessary variables for app deployment
  export NAMESPACE APP_NAME REGION
  # Export storage variables for templating
  export STORAGE_PERSISTENT_ENABLED STORAGE_PERSISTENT_SIZE STORAGE_PERSISTENT_CLASS
  # Also export platform config for status display
  export K3S_INGRESS_DOMAIN

  log "Generated app .env with intent only ($APP_ENV_PATH)"
  log "Platform variables exported for manifest templating"
}

cmd_up() {
  check_deps_basic

  # Load environment configuration
  load_environment_config

  log "Creating k3d cluster: $CLUSTER_NAME"
  if cluster_is_healthy; then
    log "Cluster already exists and is healthy. Skipping create."
  else
    # If cluster exists but is unhealthy, delete it first
    if cluster_exists; then
      log "Cluster exists but is unhealthy. Deleting and recreating..."
      k3d cluster delete "$CLUSTER_NAME" || true
    fi

    k3d cluster create "$CLUSTER_NAME" \
      --agents 1 \
      --port '80:80@loadbalancer' \
      --port '443:443@loadbalancer' \
      --k3s-arg '--disable=traefik@server:0' \
      --wait
  fi

  # Write kubeconfig and print hint
  local kubeconfig
  kubeconfig=$(k3d kubeconfig write "$CLUSTER_NAME")
  log "Kubeconfig written to: $kubeconfig"
  echo "Export it in your shell to use kubectl:"
  echo "  export KUBECONFIG=$kubeconfig"

  # Quick sanity
  log "Cluster nodes:"
  KUBECONFIG="$kubeconfig" kubectl get nodes -o wide || true
  generate_sample_app_env
}

cmd_down() {
  check_deps_basic

  # Load environment configuration to get correct CLUSTER_NAME
  load_environment_config

  log "Deleting k3d cluster: $CLUSTER_NAME"
  k3d cluster delete "$CLUSTER_NAME" || true

  # Remove generated app config
  APP_ENV_PATH="$(dirname "$0")/sample-app/.env"
  if [ -f "$APP_ENV_PATH" ]; then
    rm -f "$APP_ENV_PATH"
    log "Removed sample-app .env config ($APP_ENV_PATH)"
  fi
}

cmd_status() {
  need k3d
  log "k3d clusters:"
  k3d cluster list || true
  if command -v kubectl >/dev/null 2>&1; then
    echo
    log "Kubernetes nodes (if KUBECONFIG set):"
    kubectl get nodes -o wide || true
  fi
}

cmd_tls_up() {
  check_deps_basic

  # Load environment configuration
  load_environment_config
  require_domain
  derive_env_defaults

  log "Ensuring istio-system namespace exists"
  kubectl create namespace istio-system >/dev/null 2>&1 || true

  # Check if we can reuse existing certificate (in cluster or from backup)
  if cert_is_valid "istio-system" "$TLS_SECRET_NAME"; then
    log "Reusing valid certificate from cluster: $TLS_SECRET_NAME"
    backup_certificate "istio-system" "$TLS_SECRET_NAME"
    return 0
  fi

  # If not in cluster, try to restore from backup
  if cert_is_valid_from_backup "istio-system" "$TLS_SECRET_NAME"; then
    if restore_certificate "istio-system" "$TLS_SECRET_NAME"; then
      log "Reusing valid certificate from backup: $TLS_SECRET_NAME"
      return 0
    fi
  fi

  # Check if we have cert-manager and CloudFlare credentials
  if has_certmanager_config; then
    log "Using cert-manager with CloudFlare DNS01 for *.${K3S_INGRESS_DOMAIN}"
    setup_certmanager_tls
  else
    log "Using self-signed certificate for *.${K3S_INGRESS_DOMAIN}"
    setup_selfsigned_tls
  fi
}

has_certmanager_config() {
  # Check if cert-manager is installed
  kubectl -n cert-manager get deploy cert-manager >/dev/null 2>&1 || return 1

  # Check if we have CloudFlare credentials
  [ -n "${CLOUDFLARE_EMAIL:-}" ] && [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ZONE_ID:-}" ]
}

setup_certmanager_tls() {
  # Create CloudFlare credentials secret
  log "Creating CloudFlare credentials secret..."
  kubectl create secret generic cloudflare-api-token-secret -n cert-manager \
    --from-literal=api-token="$CLOUDFLARE_API_TOKEN" --dry-run=client -o yaml | kubectl apply -f -

  # Apply ClusterIssuers
  log "Applying ClusterIssuers..."
  export CLOUDFLARE_EMAIL CLOUDFLARE_API_TOKEN CLOUDFLARE_ZONE_ID
  apply_manifest_template "certmgr/cluster-issuers-template.yaml"

  # Wait for issuer to be ready
  local issuer_name="letsencrypt-staging"
  if [ "${USE_PROD_CERTS:-false}" = "true" ]; then
    issuer_name="letsencrypt-prod"
  fi

  log "Waiting for ClusterIssuer $issuer_name to be ready..."
  kubectl wait --for=condition=Ready "clusterissuer/$issuer_name" --timeout=120s || \
    fail "ClusterIssuer $issuer_name not ready"

  # Request new wildcard certificate
  log "Requesting new wildcard certificate for *.${K3S_INGRESS_DOMAIN}..."

  local cert_name="${DETECTED_ENV:-dev}-wildcard-cert"
  export SSL_ISSUER_NAME="$issuer_name"
  export CERT_NAME="$cert_name"
  export TLS_SECRET_NAME

  apply_manifest_template "certmgr/wildcard-certificate-template.yaml"
  wait_for_certificate "istio-system" "$cert_name" 600

  # Back up the newly created certificate for future cluster resets
  backup_certificate "istio-system" "$TLS_SECRET_NAME"

  log "Let's Encrypt certificate ready: istio-system/$TLS_SECRET_NAME"
}

setup_selfsigned_tls() {
  log "Creating self-signed wildcard TLS secret: $TLS_SECRET_NAME for *.${K3S_INGRESS_DOMAIN}"
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' EXIT
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$tmpdir/tls.key" -out "$tmpdir/tls.crt" \
    -subj "/CN=*.${K3S_INGRESS_DOMAIN}" \
    -addext "subjectAltName = DNS:*.${K3S_INGRESS_DOMAIN}, DNS:${K3S_INGRESS_DOMAIN}" >/dev/null 2>&1 || \
    fail "OpenSSL failed to create self-signed cert"

  kubectl -n istio-system create secret tls "$TLS_SECRET_NAME" \
    --key "$tmpdir/tls.key" --cert "$tmpdir/tls.crt" \
    --dry-run=client -o yaml | kubectl apply -f -

  log "Self-signed TLS secret ready: istio-system/$TLS_SECRET_NAME"
}

cmd_istio_up() {
  check_deps_basic
  log "Installing Istio service mesh"

  # Check if istioctl is available
  if ! command -v istioctl >/dev/null 2>&1; then
    fail "istioctl not found. Please install Istio CLI first."
  fi

  # Install Istio with full demo environment (control plane + ingress gateway)
  log "Installing Istio with demo profile"
  istioctl install --set profile=demo -y

  # Wait for Istio deployment to be ready
  log "Waiting for Istio control plane"
  kubectl -n istio-system rollout status deploy/istiod --timeout=300s

  log "Waiting for Istio ingress gateway"
  kubectl -n istio-system rollout status deploy/istio-ingressgateway --timeout=300s

  log "Istio installed successfully"
}

cmd_certmgr_up() {
  check_deps_basic
  log "Installing cert-manager"

  # Check if cert-manager is already installed
  if kubectl -n cert-manager get deploy cert-manager >/dev/null 2>&1; then
    log "cert-manager is already installed, checking readiness"
    kubectl -n cert-manager rollout status deploy/cert-manager --timeout=60s
    kubectl -n cert-manager rollout status deploy/cert-manager-webhook --timeout=60s
    kubectl -n cert-manager rollout status deploy/cert-manager-cainjector --timeout=60s
    log "cert-manager is ready"
    return 0
  fi

  # Install cert-manager
  kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.18.2/cert-manager.yaml

  log "Waiting for cert-manager to be ready"
  kubectl -n cert-manager rollout status deploy/cert-manager --timeout=300s
  kubectl -n cert-manager rollout status deploy/cert-manager-webhook --timeout=300s
  kubectl -n cert-manager rollout status deploy/cert-manager-cainjector --timeout=300s

  # Configure cert-manager to use external DNS servers for propagation checks
  log "Configuring cert-manager DNS settings..."
  kubectl patch deployment cert-manager -n cert-manager --type='json' -p='[
    {
      "op": "replace",
      "path": "/spec/template/spec/containers/0/args",
      "value": [
        "--v=2",
        "--cluster-resource-namespace=$(POD_NAMESPACE)",
        "--leader-election-namespace=kube-system",
        "--acme-http01-solver-image=quay.io/jetstack/cert-manager-acmesolver:v1.18.2",
        "--max-concurrent-challenges=60",
        "--dns01-recursive-nameservers=1.1.1.1:53,8.8.8.8:53",
        "--dns01-recursive-nameservers-only"
      ]
    }
  ]'
  kubectl rollout status deployment/cert-manager -n cert-manager --timeout=300s

  log "cert-manager installed and configured successfully"
}

ensure_region_ns() {
  local ns=$1 region=$2
  kubectl get ns "$ns" >/dev/null 2>&1 || kubectl create ns "$ns"
  kubectl label ns "$ns" istio-injection=enabled --overwrite
  kubectl label ns "$ns" compliance.region="$region" --overwrite
}

apply_region_policies() {
  local ns=$1
  # If Istio CRDs are missing, skip policy application gracefully
  if ! kubectl get crd peerauthentications.security.istio.io >/dev/null 2>&1; then
    echo "WARN: Istio CRDs not found. Skipping mTLS/AuthZ policies in namespace ${ns}. Run 'istio up' first." >&2
    return 0
  fi
  # STRICT mTLS
  cat <<EOF | kubectl apply -f -
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: ${ns}
spec:
  mtls:
    mode: STRICT
EOF

  # Minimal allow policy for ingress (can be refined as needed)
  cat <<EOF | kubectl apply -f -
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

  # Baseline NetworkPolicy
  cat <<EOF | kubectl apply -f -
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
}

cmd_regions_up() {
  check_deps_basic
  log "Creating region namespaces and applying zero-trust policies (if Istio installed)"
  ensure_region_ns region-us us
  ensure_region_ns region-eu eu
  ensure_region_ns region-ap ap

  apply_region_policies region-us
  apply_region_policies region-eu
  apply_region_policies region-ap

  log "Regions ready: region-us, region-eu, region-ap"
}

cmd_gateway_up() {
  check_deps_basic
  require_domain
  derive_env_defaults

  log "Applying wildcard gateway ${GATEWAY_NAME} for *.${K3S_INGRESS_DOMAIN} using TLS secret ${TLS_SECRET_NAME}"
  TMPFILE=$(mktemp)
  export GATEWAY_NAME K3S_INGRESS_DOMAIN TLS_SECRET_NAME
  envsubst < "$(dirname "$0")/manifests/gateway/wildcard-gateway-template.yaml" > "$TMPFILE"
  kubectl apply -f "$TMPFILE"
  rm -f "$TMPFILE"

  kubectl -n istio-system get gateway "$GATEWAY_NAME" -o yaml >/dev/null
  log "Gateway applied: istio-system/${GATEWAY_NAME}"
}

print_check() { # args: status msg
  local status=$1; shift
  if [ "$status" = OK ]; then
    echo "[ OK ] $*"
  else
    echo "[FAIL] $*" >&2
  fi
}

cmd_validate() {
  check_deps_basic
  load_environment_config
  derive_env_defaults
  require_domain

  local ok=0

  echo "== Environment Configuration =="
  print_check OK "Environment: ${DETECTED_ENV:-unknown}"
  print_check OK "Domain: $K3S_INGRESS_DOMAIN"
  print_check OK "TLS Secret: $TLS_SECRET_NAME"

  echo "\n== Cluster =="
  if kubectl cluster-info >/dev/null 2>&1; then
    print_check OK "kubectl can reach the cluster"
  else
    print_check FAIL "kubectl cannot reach the cluster (set KUBECONFIG?)"; ok=1
  fi
  kubectl get nodes -o wide || true

  echo "\n== cert-manager (optional) =="
  if kubectl -n cert-manager get deploy cert-manager >/dev/null 2>&1; then
    print_check OK "cert-manager deployment exists"
    kubectl -n cert-manager rollout status deploy/cert-manager --timeout=1s >/dev/null 2>&1 && \
      print_check OK "cert-manager ready" || print_check FAIL "cert-manager not ready"

    # Check ClusterIssuers
    if kubectl get clusterissuer letsencrypt-staging >/dev/null 2>&1; then
      local staging_ready=$(kubectl get clusterissuer letsencrypt-staging -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
      [ "$staging_ready" = "True" ] && \
        print_check OK "ClusterIssuer letsencrypt-staging ready" || \
        print_check FAIL "ClusterIssuer letsencrypt-staging not ready: $staging_ready"
    else
      print_check FAIL "ClusterIssuer letsencrypt-staging not found"
    fi

    if kubectl get clusterissuer letsencrypt-prod >/dev/null 2>&1; then
      local prod_ready=$(kubectl get clusterissuer letsencrypt-prod -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
      [ "$prod_ready" = "True" ] && \
        print_check OK "ClusterIssuer letsencrypt-prod ready" || \
        print_check FAIL "ClusterIssuer letsencrypt-prod not ready: $prod_ready"
    else
      print_check FAIL "ClusterIssuer letsencrypt-prod not found"
    fi
  else
    print_check OK "cert-manager not installed (using self-signed certificates)"
  fi

  echo "\n== Istio Control Plane =="
  if kubectl -n istio-system get deploy istiod >/dev/null 2>&1; then
    print_check OK "istiod deployment exists"
    kubectl -n istio-system rollout status deploy/istiod --timeout=1s >/dev/null 2>&1 && \
      print_check OK "istiod rollout ready" || print_check FAIL "istiod not ready"
  else
    print_check FAIL "istiod deployment not found"; ok=1
  fi
  if kubectl -n istio-system get deploy istio-ingressgateway >/dev/null 2>&1; then
    print_check OK "istio-ingressgateway deployment exists"
    kubectl -n istio-system rollout status deploy/istio-ingressgateway --timeout=1s >/dev/null 2>&1 && \
      print_check OK "ingressgateway rollout ready" || print_check FAIL "ingressgateway not ready"
  else
    print_check FAIL "istio-ingressgateway deployment not found"; ok=1
  fi

  echo "\n== TLS Certificate =="
  if kubectl -n istio-system get secret "$TLS_SECRET_NAME" >/dev/null 2>&1; then
    if cert_is_valid "istio-system" "$TLS_SECRET_NAME"; then
      print_check OK "TLS certificate present and valid (>7 days): istio-system/$TLS_SECRET_NAME"

      # Show certificate details
      local cert_data=$(kubectl -n istio-system get secret "$TLS_SECRET_NAME" -o jsonpath='{.data.tls\.crt}' 2>/dev/null)
      if [ -n "$cert_data" ]; then
        local tmp=$(mktemp)
        echo "$cert_data" | base64 -d > "$tmp"
        local expiry=$(openssl x509 -enddate -noout -in "$tmp" 2>/dev/null | cut -d= -f2)
        local issuer=$(openssl x509 -issuer -noout -in "$tmp" 2>/dev/null | sed 's/issuer=//')
        rm -f "$tmp"
        echo "      Expires: $expiry"
        echo "      Issuer: $issuer"
      fi
    else
      print_check FAIL "TLS certificate exists but expires soon (<7 days): istio-system/$TLS_SECRET_NAME"
      ok=1
    fi

    # Check if there's an associated Certificate resource
    local cert_name="${DETECTED_ENV:-dev}-wildcard-cert"
    if kubectl -n istio-system get certificate "$cert_name" >/dev/null 2>&1; then
      local cert_ready=$(kubectl -n istio-system get certificate "$cert_name" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
      [ "$cert_ready" = "True" ] && \
        print_check OK "Certificate resource ready: $cert_name" || \
        print_check FAIL "Certificate resource not ready: $cert_name ($cert_ready)"
    fi
  else
    print_check FAIL "TLS secret missing: istio-system/$TLS_SECRET_NAME"; ok=1
  fi

  # Check for certificate backup
  if [ -f "cluster-state/${TLS_SECRET_NAME}.yaml" ]; then
    if cert_is_valid_from_backup "istio-system" "$TLS_SECRET_NAME"; then
      print_check OK "Certificate backup available and valid: cluster-state/${TLS_SECRET_NAME}.yaml"
    else
      print_check OK "Certificate backup exists but expired: cluster-state/${TLS_SECRET_NAME}.yaml"
    fi
  else
    print_check OK "No certificate backup found (will be created automatically)"
  fi

  echo "\n== Regions & Policies =="
  for ns in region-us:us region-eu:eu region-ap:ap; do
    rns=${ns%:*}; r=${ns#*:}
    if kubectl get ns "$rns" >/dev/null 2>&1; then
      print_check OK "namespace exists: $rns"
      lbl=$(kubectl get ns "$rns" -o jsonpath='{.metadata.labels.compliance\.region}' 2>/dev/null || echo "")
      [ "$lbl" = "$r" ] && print_check OK "$rns labeled compliance.region=$r" || print_check FAIL "$rns missing compliance.region=$r" && ok=1
      kubectl -n "$rns" get peerauthentication default >/dev/null 2>&1 && \
        print_check OK "$rns PeerAuthentication present" || { print_check FAIL "$rns PeerAuthentication missing"; ok=1; }
      kubectl -n "$rns" get authorizationpolicy allow-ingress >/dev/null 2>&1 && \
        print_check OK "$rns AuthorizationPolicy allow-ingress present" || { print_check FAIL "$rns AuthorizationPolicy missing"; ok=1; }
      kubectl -n "$rns" get netpol baseline-istio-access >/dev/null 2>&1 && \
        print_check OK "$rns NetworkPolicy baseline-istio-access present" || { print_check FAIL "$rns NetworkPolicy missing"; ok=1; }
    else
      print_check FAIL "namespace missing: $rns"; ok=1
    fi
  done

  echo "\n== Gateway =="
  if kubectl -n istio-system get gateway "$GATEWAY_NAME" >/dev/null 2>&1; then
    print_check OK "Gateway present: istio-system/$GATEWAY_NAME (hosts *.${K3S_INGRESS_DOMAIN})"
  else
    print_check FAIL "Gateway missing: istio-system/$GATEWAY_NAME"; ok=1
  fi
  kubectl -n istio-system get svc istio-ingressgateway -o wide || true

  echo "\n== Storage Platform (optional) =="
  if kubectl get ns openebs-system >/dev/null 2>&1; then
    print_check OK "OpenEBS namespace exists"

    # Check OpenEBS provisioner
    if kubectl -n openebs-system get deploy openebs-localpv-provisioner >/dev/null 2>&1; then
      kubectl -n openebs-system rollout status deploy/openebs-localpv-provisioner --timeout=1s >/dev/null 2>&1 && \
        print_check OK "OpenEBS LocalPV provisioner ready" || print_check FAIL "OpenEBS LocalPV provisioner not ready"
    else
      print_check FAIL "OpenEBS LocalPV provisioner not found"
    fi

    # Check enterprise storage classes
    local storage_classes=$(kubectl get sc -l compliance.storage/managed-by=enterprise-sim --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$storage_classes" -gt 0 ]; then
      print_check OK "Enterprise storage classes available ($storage_classes classes)"
      kubectl get sc -l compliance.storage/managed-by=enterprise-sim --no-headers | while read sc_name sc_provisioner sc_reclaim sc_binding sc_expansion sc_age; do
        local tier=$(kubectl get sc "$sc_name" -o jsonpath='{.metadata.labels.compliance\.storage/tier}' 2>/dev/null)
        echo "      - $sc_name (tier: $tier)"
      done
    else
      print_check FAIL "No enterprise storage classes found"
    fi

    # Check for active PVCs
    local pvc_count=$(kubectl get pvc --all-namespaces --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$pvc_count" -gt 0 ]; then
      print_check OK "Active persistent volume claims ($pvc_count PVCs)"
    else
      print_check OK "No persistent volume claims (storage available when needed)"
    fi
  else
    print_check OK "OpenEBS not installed (install with './enterprise-sim.sh storage up')"
  fi

  echo
  if [ $ok -eq 0 ]; then
    echo "All core checks passed. You can now add routes or deploy sample services."
    exit 0
  else
    echo "Some checks failed. See messages above." >&2
    exit 1
  fi
}

cmd_app_deploy() {
  check_deps_basic
  need_envsubst

  # Load environment configuration first
  load_environment_config

  log "Deploying sample application with platform configuration"

  # Generate app environment and export platform variables
  generate_sample_app_env

  APP_DIR="$(dirname "$0")/sample-app"
  if [ ! -d "$APP_DIR" ]; then
    fail "Sample app directory not found: $APP_DIR"
  fi

  # Create temporary directory for templated manifests
  TEMP_DIR=$(mktemp -d)
  trap 'rm -rf "$TEMP_DIR"' EXIT

  log "Templating manifests with platform variables"

  # Template each manifest file
  for manifest in deployment.yaml service.yaml destinationrule.yaml; do
    if [ -f "$APP_DIR/$manifest" ]; then
      envsubst < "$APP_DIR/$manifest" > "$TEMP_DIR/$manifest"
    fi
  done

  # Add storage configuration to deployment if enabled
  if [ "${STORAGE_PERSISTENT_ENABLED:-false}" = "true" ]; then
    log "Storage enabled - adding volume mounts to deployment"
    # Append storage volume mounts and volumes to deployment
    cat >> "$TEMP_DIR/deployment.yaml" <<EOF
        volumeMounts:
        - name: app-data
          mountPath: /app/data
      volumes:
      - name: app-data
        persistentVolumeClaim:
          claimName: ${APP_NAME}-storage
EOF
  fi

  # Template PVC if storage is enabled
  if [ "${STORAGE_PERSISTENT_ENABLED:-false}" = "true" ]; then
    log "Storage enabled - creating PersistentVolumeClaim"
    if [ -f "$APP_DIR/pvc.yaml" ]; then
      envsubst < "$APP_DIR/pvc.yaml" > "$TEMP_DIR/pvc.yaml"
    fi
  fi

  # Create ConfigMap from app .env
  kubectl create configmap sample-app-env --from-env-file="$APP_DIR/.env" \
    --namespace="$NAMESPACE" --dry-run=client -o yaml > "$TEMP_DIR/configmap.yaml"

  log "Applying templated manifests"
  kubectl apply -f "$TEMP_DIR/"

  # Wait for deployment
  log "Waiting for app deployment to be ready"
  kubectl -n "$NAMESPACE" rollout status deploy/"$APP_NAME" --timeout=180s

  # Auto-generate VirtualService from Service labels
  log "Generating external routing via platform"
  cmd_routes_reconcile

  # Show app status
  echo
  log "Application deployed successfully:"
  echo "  Namespace: $NAMESPACE"
  echo "  URL: https://${REGION}-${APP_NAME}.${K3S_INGRESS_DOMAIN}"
  echo "  Status:"
  kubectl -n "$NAMESPACE" get pods,svc,virtualservice -l app="$APP_NAME"
}

cmd_routes_reconcile() {
  check_deps_basic
  need_envsubst
  derive_env_defaults
  require_domain

  echo "== Reconciling routes from Services (compliance.routing/enabled=true) =="

  # Build a map of namespace -> region
  ns_json=$(kubectl get ns -o json)
  # shellcheck disable=SC2016
  ns_map=$(echo "$ns_json" | jq -r '.items[] | "\(.metadata.name)=\(.metadata.labels["compliance.region"] // "")"')

  # Get all labeled services across namespaces
  svcs_json=$(kubectl get svc -A -l compliance.routing/enabled=true -o json)
  count=$(echo "$svcs_json" | jq '.items | length')
  if [ "$count" -eq 0 ]; then
    echo "No Services found with label compliance.routing/enabled=true. Nothing to do."
    return 0
  fi

  tmpl="$(dirname "$0")/manifests/routing/virtualservice-template.yaml"
  [ -f "$tmpl" ] || fail "Template not found: $tmpl"

  changed=0
  echo "$svcs_json" | jq -c '.items[]' | while read -r item; do
    ns=$(echo "$item" | jq -r '.metadata.namespace')
    name=$(echo "$item" | jq -r '.metadata.name')

    # Determine region from namespace labels
    region=$(echo "$ns_map" | awk -F= -v n="$ns" '$1==n{print $2}')
    if [ -z "$region" ] || [ "$region" = "null" ]; then
      echo "WARN: Namespace $ns has no compliance.region label; skipping Service $ns/$name" >&2
      continue
    fi

    # Determine app host component
    app_host=$(echo "$item" | jq -r '.metadata.labels["compliance.routing/host"] // .metadata.annotations["compliance.routing/host"] // .metadata.name')

    # Determine service port to route to
    lbl_port=$(echo "$item" | jq -r '.metadata.labels["compliance.routing/port"] // .metadata.annotations["compliance.routing/port"] // ""')
    if [ -n "$lbl_port" ] && [ "$lbl_port" != "null" ]; then
      svc_port="$lbl_port"
    else
      svc_port=$(echo "$item" | jq -r '.spec.ports[0].port // empty')
    fi
    if [ -z "$svc_port" ] || [ "$svc_port" = "null" ]; then
      echo "WARN: Could not determine Service port for $ns/$name; skipping" >&2
      continue
    fi

    VS_NAME="route-${name}"
    VS_NAMESPACE="$ns"
    SVC_HOST="$name"
    SVC_PORT="$svc_port"
    VS_HOST="${region}-${app_host}.${K3S_INGRESS_DOMAIN}"

    export VS_NAME VS_NAMESPACE SVC_HOST SVC_PORT VS_HOST K3S_INGRESS_DOMAIN GATEWAY_NAME

    tmp=$(mktemp)
    envsubst < "$tmpl" > "$tmp"
    if kubectl apply -f "$tmp" >/dev/null; then
      echo "Applied/updated VirtualService: $VS_NAMESPACE/$VS_NAME (host: $VS_HOST -> $SVC_HOST:$SVC_PORT)"
      changed=$((changed+1))
    else
      echo "ERROR: Failed to apply VirtualService for $ns/$name" >&2
    fi
    rm -f "$tmp"
  done

  echo "Reconciliation complete."
}

cmd_configure() {
  log "Configuring CloudFlare credentials for Let's Encrypt certificates"
  echo
  echo "You need CloudFlare API credentials to use Let's Encrypt certificates with DNS01 validation."
  echo "Get these from: https://dash.cloudflare.com/profile/api-tokens"
  echo

  read -p "CloudFlare email: " cf_email
  read -sp "CloudFlare API token: " cf_token
  echo
  read -p "CloudFlare Zone ID: " cf_zone_id
  read -p "Domain for this environment (e.g., dev.${BASE_DOMAIN}): " cf_domain

  [ -n "$cf_email" ] && [ -n "$cf_token" ] && [ -n "$cf_zone_id" ] && [ -n "$cf_domain" ] || \
    fail "All fields are required"

  # Detect environment from domain or ask user
  local env=""
  case "$cf_domain" in
    local.${BASE_DOMAIN}|localhost|*.local) env="local" ;;
    dev.${BASE_DOMAIN}|dev.*|*.dev.*) env="dev" ;;
    staging.${BASE_DOMAIN}|staging.*|*.staging.*|stage.*) env="staging" ;;
    prod.${BASE_DOMAIN}|prod.*|*.prod.*) env="prod" ;;
    *)
      echo
      echo "Select environment for this domain:"
      echo "1) local (self-signed certificates)"
      echo "2) dev (Let's Encrypt staging)"
      echo "3) staging (Let's Encrypt staging)"
      echo "4) prod (Let's Encrypt production)"
      read -p "Choice (1-4): " env_choice
      case "$env_choice" in
        1) env="local" ;;
        2) env="dev" ;;
        3) env="staging" ;;
        4) env="prod" ;;
        *) fail "Invalid choice" ;;
      esac
      ;;
  esac

  local config_file="config/enterprise-sim.${env}"
  log "Creating configuration file: $config_file"

  # Create config directory if it doesn't exist
  mkdir -p config

  # Capitalize first letter for display (portable way)
  local first_char="$(echo "$env" | cut -c1 | tr '[:lower:]' '[:upper:]')"
  local rest_chars="$(echo "$env" | cut -c2-)"
  local env_display="${first_char}${rest_chars}"

  cat > "$config_file" <<EOF
# Enterprise Simulation - ${env_display} Configuration
# Generated on $(date)

# Domain configuration
export K3S_INGRESS_DOMAIN="$cf_domain"

# Certificate configuration
export USE_PROD_CERTS=$([ "$env" = "prod" ] && echo "true" || echo "false")

# CloudFlare configuration for Let's Encrypt
export CLOUDFLARE_EMAIL="$cf_email"
export CLOUDFLARE_API_TOKEN="$cf_token"
export CLOUDFLARE_ZONE_ID="$cf_zone_id"

# Environment settings
export ENVIRONMENT=$env
export CLUSTER_NAME=enterprise-sim-$env
EOF

  log "Configuration saved successfully!"
  echo
  echo "Next steps:"
  echo "1. Install cert-manager: ./enterprise-sim.sh certmgr up"
  echo "2. Create cluster: ./enterprise-sim.sh up"
  echo "3. Setup TLS: ./enterprise-sim.sh tls up"
  echo
  echo "Or run everything at once: ./enterprise-sim.sh full-up"
}

cmd_full_up() {
  log "[full-up] Cluster up..."
  cmd_up

  log "[full-up] Installing Istio..."
  cmd_istio_up

  # Only install cert-manager if we have CloudFlare config
  if [ -n "${CLOUDFLARE_EMAIL:-}" ] && [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    log "[full-up] Installing cert-manager..."
    cmd_certmgr_up
  else
    log "[full-up] Skipping cert-manager (no CloudFlare config found)"
  fi

  log "[full-up] Setting up TLS certificates..."
  cmd_tls_up

  log "[full-up] Creating region namespaces/policies..."
  cmd_regions_up

  log "[full-up] Installing gateway..."
  cmd_gateway_up

  log "[full-up] Platform base build complete."
  echo
  echo "Next steps:"
  echo "- Deploy sample app: $0 app deploy"
  echo "- Auto-generate routes: $0 routes reconcile"
  echo "- Validate system: $0 validate"
}

cmd_storage_up() {
  check_deps_basic
  check_deps_helm

  # Load environment configuration
  load_environment_config

  log "Installing OpenEBS storage platform..."

  # Add OpenEBS Helm repository
  log "Adding OpenEBS Helm repository..."
  helm repo add openebs https://openebs.github.io/charts >/dev/null 2>&1 || true
  helm repo update >/dev/null 2>&1

  # Install OpenEBS
  log "Installing OpenEBS LocalPV provisioner..."
  helm upgrade --install openebs openebs/openebs \
    --namespace openebs-system \
    --create-namespace \
    --set engines.local.lvm.enabled=false \
    --set engines.local.zfs.enabled=false \
    --set engines.replicated.mayastor.enabled=false \
    --set engines.local.hostpath.enabled=true \
    --set localpv-provisioner.hostpathClass.enabled=true \
    --set localpv-provisioner.hostpathClass.name=openebs-hostpath \
    --set localpv-provisioner.hostpathClass.isDefaultClass=false \
    --wait --timeout=300s

  # Wait for OpenEBS components to be ready
  log "Waiting for OpenEBS components to be ready..."
  kubectl -n openebs-system wait --for=condition=ready pod --all --timeout=180s

  # Apply storage classes
  log "Creating enterprise storage classes..."
  apply_storage_classes

  log "OpenEBS storage platform installed successfully!"
  echo
  echo "Available Storage Classes:"
  kubectl get storageclass -l compliance.storage/managed-by=enterprise-sim
}

apply_storage_classes() {
  TMPFILE=$(mktemp)
  trap 'rm -f "$TMPFILE"' EXIT

  cat > "$TMPFILE" <<'EOF'
---
# Standard performance storage class
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: enterprise-standard
  labels:
    compliance.storage/managed-by: enterprise-sim
    compliance.storage/tier: standard
    compliance.storage/encryption: enabled
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: openebs.io/local
volumeBindingMode: WaitForFirstConsumer
parameters:
  storageType: hostpath
  basePath: "/var/openebs/local"
reclaimPolicy: Delete
---
# SSD performance storage class
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: enterprise-ssd
  labels:
    compliance.storage/managed-by: enterprise-sim
    compliance.storage/tier: ssd
    compliance.storage/encryption: enabled
provisioner: openebs.io/local
volumeBindingMode: WaitForFirstConsumer
parameters:
  storageType: hostpath
  basePath: "/var/openebs/ssd"
reclaimPolicy: Delete
---
# Fast performance storage class
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: enterprise-fast
  labels:
    compliance.storage/managed-by: enterprise-sim
    compliance.storage/tier: fast
    compliance.storage/encryption: enabled
provisioner: openebs.io/local
volumeBindingMode: WaitForFirstConsumer
parameters:
  storageType: hostpath
  basePath: "/var/openebs/fast"
reclaimPolicy: Delete
EOF

  kubectl apply -f "$TMPFILE"
}

cmd_reset() {
  log "[reset] Tearing down platform (down + wipe app env/cluster)..."
  cmd_down
  cmd_full_up
}

usage() {
  cat <<EOF
Usage: $0 <command>

Commands:
  configure           Setup CloudFlare credentials for Let's Encrypt certificates
  up                  Create k3d cluster with ports 80/443 mapped (Traefik disabled)
  full-up             Full end-to-end platform build (runs up, istio, certmgr, tls, regions, gateway)
  reset               Teardown everything and re-run full-up (cluster, env, app)
  down                Delete the k3d cluster and app env file
  status              Show cluster and node status (if KUBECONFIG set)
  certmgr up          Install cert-manager for Let's Encrypt certificates
  istio up            Install Istio service mesh with demo profile
  tls up              Setup TLS certificates (auto-detects cert-manager vs self-signed)
  storage up          Install OpenEBS storage platform with enterprise storage classes
  regions up          Create region namespaces and apply zero-trust policies
  gateway up          Apply wildcard HTTPS gateway using TLS secret
  routes reconcile    Auto-generate VirtualServices from labeled Services
  app deploy          Deploy sample application with platform configuration
  validate            Validate all components are running correctly

Environment Setup (Required):
  1. Copy template:    cp config/enterprise-sim.template config/enterprise-sim.dev
  2. Edit config:      Edit config/enterprise-sim.dev with your settings
  3. Deploy:           $0 full-up  # Uses the configured environment

Safety Features:
  - Only ONE config file can exist (prevents wrong-environment deployments)
  - Config file determines environment automatically
  - Template has clear placeholders that must be changed

Manual Setup:
  1. $0 configure          # Setup environment configuration
  2. $0 up                 # Create cluster
  3. $0 istio up           # Install Istio service mesh
  4. $0 certmgr up         # Install cert-manager (optional)
  5. $0 storage up         # Install OpenEBS storage platform (optional)
  6. $0 tls up             # Setup TLS certificates
  7. $0 regions up         # Create region namespaces
  8. $0 gateway up         # Setup wildcard gateway
  9. $0 app deploy         # Deploy sample application
  10. $0 validate          # Verify everything is working

Environment Configuration:
  Environment determined by single config file in config/ directory:
  - Copy config/enterprise-sim.template to config/enterprise-sim.{env}
  - Edit the config file with your environment-specific settings
  - Only ONE config file should exist to prevent deployment errors

Environment Variables:
  BASE_DOMAIN          Base domain for all environments (default: butterflycluster.com)
  CLUSTER_NAME         Cluster name (default: enterprise-sim-{env})
  K3S_INGRESS_DOMAIN   Full domain for gateway hosts ({env}.\${BASE_DOMAIN})
  CLOUDFLARE_EMAIL     CloudFlare email for Let's Encrypt
  CLOUDFLARE_API_TOKEN CloudFlare API token for DNS01 validation
  CLOUDFLARE_ZONE_ID   CloudFlare Zone ID for the domain
  USE_PROD_CERTS       Use production Let's Encrypt (true for prod env)

Certificate Management:
  - Self-signed: Used for localhost and when CloudFlare not configured
  - Let's Encrypt Staging: Used for dev/staging environments
  - Let's Encrypt Production: Used for prod environment
  - Automatic backup/restore: Certificates survive cluster resets
  - Smart reuse: Validates certificates before requesting new ones

Dependencies:
  - k3d, kubectl, jq, openssl (basic operations)
  - istioctl (for Istio installation)
  - envsubst (for templating)

Notes:
  - After 'up', export KUBECONFIG from the path printed to use kubectl
  - Configure DNS: Point *.{env}.\${BASE_DOMAIN} to your cluster IP
  - Certificate backups are stored in cluster-state/ directory
  - Run 'validate' to check system health and certificate status
  - Override base domain: BASE_DOMAIN=mycompany.com ./enterprise-sim.sh up
  - Template config prevents accidental deployments to wrong environments
EOF
}


main() {
  case "${1:-}" in
    up) shift; cmd_up "$@" ;;
    down) shift; cmd_down "$@" ;;
    full-up) shift; cmd_full_up "$@" ;;
    reset) shift; cmd_reset "$@" ;;
    status) shift; cmd_status "$@" ;;
    tls) shift; case "${1:-}" in up) shift; cmd_tls_up "$@" ;; *) usage ;; esac ;;
    istio) shift; case "${1:-}" in up) shift; cmd_istio_up "$@" ;; *) usage ;; esac ;;
    storage) shift; case "${1:-}" in up) shift; cmd_storage_up "$@" ;; *) usage ;; esac ;;
    regions) shift; case "${1:-}" in up) shift; cmd_regions_up "$@" ;; *) usage ;; esac ;;
    gateway) shift; case "${1:-}" in up) shift; cmd_gateway_up "$@" ;; *) usage ;; esac ;;
    routes) shift; case "${1:-}" in reconcile) shift; cmd_routes_reconcile "$@" ;; *) usage ;; esac ;;
    app) shift; case "${1:-}" in deploy) shift; cmd_app_deploy "$@" ;; *) usage ;; esac ;;
    certmgr) shift; case "${1:-}" in up) shift; cmd_certmgr_up "$@" ;; *) usage ;; esac ;;
    configure) shift; cmd_configure "$@" ;;
    validate) shift; cmd_validate "$@" ;;
    -h|--help|help|*) usage ;;
  esac
}

main "$@"
