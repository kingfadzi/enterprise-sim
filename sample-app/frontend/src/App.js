import React, { useState, useEffect } from 'react';
import HealthCard from './components/HealthCard';
import SecurityCard from './components/SecurityCard';
import StorageCard from './components/StorageCard';
import ComplianceCard from './components/ComplianceCard';
import ObservabilityCard from './components/ObservabilityCard';
import DisasterRecoveryCard from './components/DisasterRecoveryCard';
import S3StorageCard from './components/S3StorageCard';
import SecretsCard from './components/SecretsCard';
import NetworkingCard from './components/NetworkingCard';
import { useApiData } from './hooks/useApiData';

function App() {
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const { data: serviceInfo } = useApiData('info');

  useEffect(() => {
    const interval = setInterval(() => {
      setLastRefresh(new Date());
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="dashboard">
      <div className="refresh-indicator">
        Last refresh: {lastRefresh.toLocaleTimeString()}
      </div>

      <header className="header">
        <h1>Enterprise Simulation Platform</h1>
        <p>
          Service: {serviceInfo?.service || 'Loading...'} |
          Region: {serviceInfo?.region || 'Loading...'} |
          Namespace: {serviceInfo?.namespace || 'Loading...'}
        </p>
      </header>

      <div className="cards-container">
        <HealthCard />
        <SecurityCard />
        <StorageCard />
        <S3StorageCard />
        <SecretsCard />
        <ObservabilityCard />
        <DisasterRecoveryCard />
        <NetworkingCard />
        <ComplianceCard />
      </div>
    </div>
  );
}

export default App;