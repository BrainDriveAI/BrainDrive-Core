import React from 'react';

interface LoadingStatesProps {
  isInitializing: boolean;
}

const LoadingStates: React.FC<LoadingStatesProps> = ({ isInitializing }) => {
  /**
   * Render initializing state
   */
  const renderInitializingState = () => {
    if (!isInitializing) return null;
    
    return (
      <div className="loading-indicator">
        <div className="loading-dots">
          <div className="loading-dot"></div>
          <div className="loading-dot"></div>
          <div className="loading-dot"></div>
        </div>
        <div className="loading-text">
          Initializing chat...
        </div>
      </div>
    );
  };

  return renderInitializingState();
};

export default LoadingStates;