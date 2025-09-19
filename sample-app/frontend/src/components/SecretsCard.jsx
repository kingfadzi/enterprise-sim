import React from 'react';
import { useApiData } from '../hooks/useApiData';

const SecretsCard = () => {
  const { data, loading, error } = useApiData('secrets');

  if (loading) return <div className="card"><div className="loading">Loading secrets management status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading secrets: {error}</div></div>;

  const secretsStatus = data?.secrets_status || {};
  const isConfigured = secretsStatus.configured;

  const getStatusClass = () => {
    if (secretsStatus.vault_available && secretsStatus.secrets_mounted) return 'status-healthy';
    if (secretsStatus.vault_available || secretsStatus.vault_configured) return 'status-warning';
    return 'status-error';
  };

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${getStatusClass()}`}></span>
        Secrets Management
      </h3>
      <div className="info-row">
        <span className="info-label">Secrets Platform:</span>
        <span className="info-value">{isConfigured ? '✅ Available' : '❌ Not Configured'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Vault Service:</span>
        <span className="info-value">{secretsStatus.vault_available ? '✅ Running' : '❌ Not Found'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Vault Config:</span>
        <span className="info-value">{secretsStatus.vault_configured ? '✅ Configured' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Secrets Mounted:</span>
        <span className="info-value">{secretsStatus.secrets_mounted ? '✅ Yes' : '❌ No'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">CSI Driver:</span>
        <span className="info-value">{secretsStatus.csi_driver ? '✅ Active' : '❌ Missing'}</span>
      </div>
      {!isConfigured && !secretsStatus.error && (
        <div className="info-row">
          <span className="info-label">Setup Required:</span>
          <span className="info-value" style={{color: '#ff9800'}}>Run: ./enterprise-sim.sh vault up</span>
        </div>
      )}
      {secretsStatus.error && (
        <div className="info-row">
          <span className="info-label">Error:</span>
          <span className="info-value" style={{color: '#f44336'}}>{secretsStatus.error}</span>
        </div>
      )}
    </div>
  );
};

export default SecretsCard;