import React from 'react';
import { useApiData } from '../hooks/useApiData';

const HealthCard = () => {
  const { data, loading, error } = useApiData('health');

  if (loading) return <div className="card"><div className="loading">Loading health status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading health: {error}</div></div>;

  const isHealthy = data?.status === 'healthy';

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${isHealthy ? 'status-healthy' : 'status-error'}`}></span>
        Health Status
      </h3>
      <div className="info-row">
        <span className="info-label">Status:</span>
        <span className="info-value">{data?.status || 'Unknown'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Service:</span>
        <span className="info-value">{data?.service || 'Unknown'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Version:</span>
        <span className="info-value">{data?.version || 'Unknown'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Last Check:</span>
        <span className="info-value">{data?.timestamp ? new Date(data.timestamp).toLocaleTimeString() : 'Unknown'}</span>
      </div>
    </div>
  );
};

export default HealthCard;