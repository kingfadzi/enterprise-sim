import React from 'react';
import { useApiData } from '../hooks/useApiData';

const S3StorageCard = () => {
  const { data, loading, error } = useApiData('s3-storage');

  if (loading) return <div className="card"><div className="loading">Loading S3 storage status...</div></div>;
  if (error) return <div className="card"><div className="error">Error loading S3 storage: {error}</div></div>;

  const s3Status = data?.s3_storage_status || {};
  const isConfigured = s3Status.configured;

  const getStatusClass = () => {
    if (s3Status.minio_available && s3Status.s3_configured) return 'status-healthy';
    if (s3Status.minio_available || s3Status.s3_configured) return 'status-warning';
    return 'status-error';
  };

  return (
    <div className="card">
      <h3>
        <span className={`status-indicator ${getStatusClass()}`}></span>
        S3 Object Storage
      </h3>
      <div className="info-row">
        <span className="info-label">Object Storage:</span>
        <span className="info-value">{isConfigured ? '✅ Available' : '❌ Not Configured'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">MinIO Service:</span>
        <span className="info-value">{s3Status.minio_available ? '✅ Running' : '❌ Not Found'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">S3 Credentials:</span>
        <span className="info-value">{s3Status.s3_configured ? '✅ Configured' : '❌ Missing'}</span>
      </div>
      <div className="info-row">
        <span className="info-label">Bucket Access:</span>
        <span className="info-value">{s3Status.bucket_access ? '✅ Verified' : '❌ Not Tested'}</span>
      </div>
      {!isConfigured && !s3Status.error && (
        <div className="info-row">
          <span className="info-label">Setup Required:</span>
          <span className="info-value" style={{color: '#ff9800'}}>Run: ./enterprise-sim.sh minio up</span>
        </div>
      )}
      {s3Status.error && (
        <div className="info-row">
          <span className="info-label">Error:</span>
          <span className="info-value" style={{color: '#f44336'}}>{s3Status.error}</span>
        </div>
      )}
    </div>
  );
};

export default S3StorageCard;