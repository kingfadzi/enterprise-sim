import React from 'react';
import { useApiData } from '../hooks/useApiData';

const DisasterRecoveryCard = () => {
  const { data, loading, error } = useApiData('disaster-recovery');

  if (loading) return <div className="card"><div className="loading">Loading disaster recovery status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading disaster recovery: {error}</div></div>;

  const drStatus = data?.disaster_recovery_status || {};
  const isConfigured = drStatus.configured;

  const getStatusClass = () => {
    if (drStatus.backup_enabled && drStatus.storage_ready) return 'status-healthy';
    if (drStatus.storage_ready) return 'status-warning';
    return 'status-error';
  };

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${getStatusClass()}`}></span>
        Disaster Recovery
      </h3>
      <div className="info-row">
        <span className="info-label">Backup System:</span>
        <span className="info-value">{drStatus.backup_enabled ? '✅ Active' : '❌ Not Configured'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Velero Installed:</span>
        <span className="info-value">{drStatus.velero_installed ? '✅ Yes' : '❌ No'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Storage Ready:</span>
        <span className="info-value">{drStatus.storage_ready ? '✅ Available' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Cross-Region Replication:</span>
        <span className="info-value">{drStatus.cross_region_replication ? '✅ Enabled' : '❌ Disabled'}</span>
      </div>
      {!isConfigured && !drStatus.error && (
        <div className="info-row">
          <span className="info-label">Setup Required:</span>
          <span className="info-value" style={{color: '#ff9800'}}>Run: ./enterprise-sim.sh backup up</span>
        </div>
      )}
      {drStatus.error && (
        <div className="info-row">
          <span className="info-label">Error:</span>
          <span className="info-value" style={{color: '#f44336'}}>{drStatus.error}</span>
        </div>
      )}
    </div>
  );
};

export default DisasterRecoveryCard;