import React from 'react';
import { useApiData } from '../hooks/useApiData';

const ObservabilityCard = () => {
  const { data, loading, error } = useApiData('observability');

  if (loading) return <div className="card"><div className="loading">Loading observability status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading observability: {error}</div></div>;

  const obsStatus = data?.observability_status || {};
  const isConfigured = obsStatus.configured;

  const getStatusClass = () => {
    if (!isConfigured) return 'status-error';
    const activeServices = [obsStatus.metrics, obsStatus.tracing, obsStatus.logging].filter(Boolean).length;
    if (activeServices >= 2) return 'status-healthy';
    if (activeServices >= 1) return 'status-warning';
    return 'status-error';
  };

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${getStatusClass()}`}></span>
        Observability Platform
      </h3>
      <div className="info-row">
        <span className="info-label">Platform Status:</span>
        <span className="info-value">{isConfigured ? '✅ Configured' : '❌ Not Configured'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Metrics (Prometheus):</span>
        <span className="info-value">{obsStatus.metrics ? '✅ Available' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Tracing (Jaeger):</span>
        <span className="info-value">{obsStatus.tracing ? '✅ Available' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Logging (ELK):</span>
        <span className="info-value">{obsStatus.logging ? '✅ Available' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Grafana Dashboards:</span>
        <span className="info-value">{obsStatus.grafana_dashboards ? '✅ Available' : '❌ Missing'}</span>
      </div>
      {obsStatus.error && (
        <div className="info-row">
          <span className="info-label">Error:</span>
          <span className="info-value" style={{color: '#f44336'}}>{obsStatus.error}</span>
        </div>
      )}
    </div>
  );
};

export default ObservabilityCard;