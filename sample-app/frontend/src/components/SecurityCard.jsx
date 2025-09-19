import React from 'react';
import { useApiData } from '../hooks/useApiData';

const SecurityCard = () => {
  const { data, loading, error } = useApiData('security');

  if (loading) return <div className="card"><div className="loading">Loading security status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading security: {error}</div></div>;

  const securityContext = data?.security_context || {};
  const networkPosture = data?.network_posture || {};

  const isSecure = !securityContext.running_as_root &&
                   networkPosture.mtls_enabled &&
                   networkPosture.zero_trust_policies;

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${isSecure ? 'status-healthy' : 'status-warning'}`}></span>
        Security Posture
      </h3>
      <div className="info-row">
        <span className="info-label">Running as Root:</span>
        <span className="info-value">{securityContext.running_as_root ? '❌ Yes' : '✅ No'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">mTLS Enabled:</span>
        <span className="info-value">{networkPosture.mtls_enabled ? '✅ Yes' : '❌ No'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Zero Trust:</span>
        <span className="info-value">{networkPosture.zero_trust_policies ? '✅ Yes' : '❌ No'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Network Policies:</span>
        <span className="info-value">{networkPosture.network_policies ? '✅ Yes' : '❌ No'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Istio Sidecar:</span>
        <span className="info-value">{networkPosture.istio_sidecar ? '✅ Present' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Service Mesh:</span>
        <span className="info-value">{networkPosture.service_mesh || 'None'}</span>
      </div>
    </div>
  );
};

export default SecurityCard;