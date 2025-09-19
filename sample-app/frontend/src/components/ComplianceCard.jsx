import React from 'react';
import { useApiData } from '../hooks/useApiData';

const ComplianceCard = () => {
  const { data, loading, error } = useApiData('posture');

  if (loading) return <div className="card"><div className="loading">Loading compliance posture...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading compliance: {error}</div></div>;

  const encryption = data?.encryption || {};
  const security = data?.security || {};
  const observability = data?.observability || {};
  const disasterRecovery = data?.disaster_recovery || {};

  const complianceScore = [
    encryption.in_transit,
    encryption.service_mesh,
    security.network?.mtls_enabled,
    security.network?.zero_trust_policies,
    observability.configured,
    disasterRecovery.configured
  ].filter(Boolean).length;

  const maxScore = 6;
  const compliancePercentage = Math.round((complianceScore / maxScore) * 100);

  const getComplianceStatus = () => {
    if (compliancePercentage >= 90) return 'status-healthy';
    if (compliancePercentage >= 70) return 'status-warning';
    return 'status-error';
  };

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${getComplianceStatus()}`}></span>
        Compliance Posture ({compliancePercentage}%)
      </h3>
      <div className="info-row">
        <span className="info-label">Compliance Tier:</span>
        <span className="info-value">{data?.compliance_tier || 'Unknown'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Encryption in Transit:</span>
        <span className="info-value">{encryption.in_transit ? '✅ Enabled' : '❌ Disabled'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Encryption at Rest:</span>
        <span className="info-value">{encryption.at_rest ? '✅ Enabled' : '❌ Disabled'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Service Mesh:</span>
        <span className="info-value">{encryption.service_mesh ? '✅ Enabled' : '❌ Disabled'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Observability:</span>
        <span className="info-value">
          {observability.configured ? '✅ Configured' : '❌ Missing'}
        </span>
      </div>
      <div className="info-row">
        <span className="info-label">Disaster Recovery:</span>
        <span className="info-value">
          {disasterRecovery.configured ? '✅ Configured' : '❌ Missing'}
        </span>
      </div>
    </div>
  );
};

export default ComplianceCard;