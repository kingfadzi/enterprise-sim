import React from 'react';
import { useApiData } from '../hooks/useApiData';

const NetworkingCard = () => {
  const { data, loading, error } = useApiData('networking');

  if (loading) return <div className="card"><div className="loading">Loading advanced networking status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading networking: {error}</div></div>;

  const netStatus = data?.networking_status || {};
  const isConfigured = netStatus.configured;

  const getStatusClass = () => {
    const activeFeatures = [netStatus.egress_policies, netStatus.waf_enabled, netStatus.service_entries].filter(Boolean).length;
    if (activeFeatures >= 2) return 'status-healthy';
    if (activeFeatures >= 1) return 'status-warning';
    return 'status-error';
  };

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${getStatusClass()}`}></span>
        Advanced Networking
      </h3>
      <div className="info-row">
        <span className="info-label">Advanced Features:</span>
        <span className="info-value">{isConfigured ? '✅ Configured' : '❌ Not Configured'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Egress Policies:</span>
        <span className="info-value">{netStatus.egress_policies ? '✅ Active' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">WAF Protection:</span>
        <span className="info-value">{netStatus.waf_enabled ? '✅ Enabled' : '❌ Disabled'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Service Entries:</span>
        <span className="info-value">{netStatus.service_entries ? '✅ Configured' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Network Segmentation:</span>
        <span className="info-value">{netStatus.network_segmentation ? '✅ Active' : '❌ Basic Only'}</span>
      </div>
      {!isConfigured && !netStatus.error && (
        <div className="info-row">
          <span className="info-label">Setup Required:</span>
          <span className="info-value" style={{color: '#ff9800'}}>Run: ./enterprise-sim.sh networking up</span>
        </div>
      )}
      {netStatus.error && (
        <div className="info-row">
          <span className="info-label">Error:</span>
          <span className="info-value" style={{color: '#f44336'}}>{netStatus.error}</span>
        </div>
      )}
    </div>
  );
};

export default NetworkingCard;