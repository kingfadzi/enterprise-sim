import React from 'react';
import { useApiData } from '../hooks/useApiData';

const StorageCard = () => {
  const { data, loading, error } = useApiData('storage');

  if (loading) return <div className="card"><div className="loading">Loading storage status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading storage: {error}</div></div>;

  const storageInfo = data?.storage_info || {};
  const isEnabled = storageInfo.enabled;

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${isEnabled ? 'status-healthy' : 'status-warning'}`}></span>
        Storage Configuration
      </h3>
      <div className="info-row">
        <span className="info-label">Persistent Storage:</span>
        <span className="info-value">{isEnabled ? '✅ Enabled' : '❌ Disabled'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Storage Type:</span>
        <span className="info-value">{storageInfo.type || 'Unknown'}</span>
      </div>
      {storageInfo.mounted !== undefined && (
        <div className="info-row">
          <span className="info-label">Mounted:</span>
          <span className="info-value">{storageInfo.mounted ? '✅ Yes' : '❌ No'}</span>
        </div>
      )}
      {storageInfo.path && (
        <div className="info-row">
          <span className="info-label">Mount Path:</span>
          <span className="info-value">{storageInfo.path}</span>
        </div>
      )}
      <div className="info-row">
        <span className="info-label">Writable:</span>
        <span className="info-value">{storageInfo.writable ? '✅ Yes' : '❌ No'}</span>
      </div>
      {storageInfo.error && (
        <div className="info-row">
          <span className="info-label">Error:</span>
          <span className="info-value" style={{color: '#f44336'}}>{storageInfo.error}</span>
        </div>
      )}
    </div>
  );
};

export default StorageCard;