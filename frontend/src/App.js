import React, { useState, useRef } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import './App.css';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import ReelGenerator from './components/ReelGenerator';
import Login from './pages/Login';
import CreditsModal from './components/CreditsModal';
import InteractiveCloth from './components/InteractiveCloth';
import VideoBackground from './components/VideoBackground';

// Helper function to format decimal minutes to MM:SS format
function formatMinutesToTimeString(minutes) {
  if (!minutes || minutes === 0) return '0:00';
  
  const totalSeconds = Math.round(minutes * 60);
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function ProtectedRoute({ children }) {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="loading-container">
        <div className="spinner"></div>
        <p>Loading...</p>
      </div>
    );
  }

  return isAuthenticated ? children : <Navigate to="/login" />;
}

function AppContent() {
  const { user, logout, isAuthenticated } = useAuth();
  const [isCreditsModalOpen, setIsCreditsModalOpen] = useState(false);
  const interactiveClothRef = useRef(null);

  return (
    <div className="App">
      <VideoBackground />
      <InteractiveCloth ref={interactiveClothRef} />
      <header className="App-header">
        <div className="header-content">
          <div className="header-left">
            <h1 className="brand-name">
              SIZA
              <img src="/tesoura.png" alt="SIZA Icon" className="brand-icon" />
            </h1>
            <p>Create engaging video reels from YouTube, personal and Other</p>
          </div>
          {isAuthenticated && user && (
            <div className="header-right">
              <div className="user-info">
                <span 
                  className="credit-badge clickable" 
                  onClick={() => setIsCreditsModalOpen(true)}
                  title="Click to buy credits"
                >
                  Credits: {user.credit}
                </span>
                <span className="minutes-info">
                  Total Time: {formatMinutesToTimeString(user.total_minutes_downloaded)}
                </span>
              </div>
              <button onClick={logout} className="logout-button">
                Logout
              </button>
            </div>
          )}
        </div>
      </header>
      <main>
        <Routes>
          <Route 
            path="/login" 
            element={isAuthenticated ? <Navigate to="/" /> : <Login />} 
          />
          <Route 
            path="/" 
            element={
              <ProtectedRoute>
                <ReelGenerator interactiveClothRef={interactiveClothRef} />
              </ProtectedRoute>
            } 
          />
        </Routes>
      </main>
      <CreditsModal 
        isOpen={isCreditsModalOpen} 
        onClose={() => setIsCreditsModalOpen(false)} 
      />
    </div>
  );
}

function App() {
  return (
    <Router>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </Router>
  );
}

export default App;

