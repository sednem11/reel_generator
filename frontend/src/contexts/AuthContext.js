import React, { createContext, useState, useContext, useEffect } from 'react';
import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

// Set ngrok bypass header globally for all axios requests
axios.defaults.headers.common['ngrok-skip-browser-warning'] = 'true';

const AuthContext = createContext(null);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState(localStorage.getItem('token'));

  // Configure axios interceptor to add token to all requests
  useEffect(() => {
    const fetchUserData = async () => {
      if (token) {
        console.log('Setting up axios with token, API_BASE_URL:', API_BASE_URL);
        axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
        localStorage.setItem('token', token);
        // Fetch user info
        await fetchUser();
      } else {
        console.log('No token, clearing auth');
        delete axios.defaults.headers.common['Authorization'];
        // Keep ngrok header even when logged out
        localStorage.removeItem('token');
        setUser(null);
        setLoading(false);
      }
    };
    
    fetchUserData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Handle axios response interceptor for 401 errors
  useEffect(() => {
    const interceptor = axios.interceptors.response.use(
      (response) => response,
      (error) => {
        if (error.response?.status === 401) {
          logout();
        }
        return Promise.reject(error);
      }
    );

    return () => {
      axios.interceptors.response.eject(interceptor);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchUser = async () => {
    try {
      console.log('Fetching user from:', `${API_BASE_URL}/api/auth/me`);
      console.log('Token exists:', !!token);
      const response = await axios.get(`${API_BASE_URL}/api/auth/me`);
      console.log('User data received:', response.data);
      setUser({
        user_id: response.data.user_id,
        credit: response.data.credit,
        total_minutes_downloaded: response.data.total_minutes_downloaded
      });
    } catch (error) {
      console.error('Error fetching user:', error);
      console.error('Error response:', error.response);
      console.error('Error status:', error.response?.status);
      console.error('Error data:', error.response?.data);
      
      // Don't immediately log out on 400/500 errors - might be temporary
      // Only log out on 401 (unauthorized)
      if (error.response?.status === 401) {
        console.log('401 Unauthorized - logging out');
        setToken(null);
        setUser(null);
      } else {
        // For other errors, keep the token but show error
        console.warn('Non-401 error when fetching user, keeping token');
      }
    } finally {
      setLoading(false);
    }
  };

  const login = async (email, password) => {
    try {
      const response = await axios.post(`${API_BASE_URL}/api/auth/login`, {
        email,
        password
      });
      setToken(response.data.token);
      // fetchUser will be called automatically by the useEffect
      return { success: true };
    } catch (error) {
      return { 
        success: false, 
        error: error.response?.data?.detail || error.message || 'Login failed' 
      };
    }
  };

  const register = async (email, password) => {
    try {
      const response = await axios.post(`${API_BASE_URL}/api/auth/register`, {
        email,
        password
      });
      setToken(response.data.token);
      // fetchUser will be called automatically by the useEffect
      return { success: true };
    } catch (error) {
      return { 
        success: false, 
        error: error.response?.data?.detail || error.message || 'Registration failed' 
      };
    }
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('token');
  };

  const refreshUser = async () => {
    if (token) {
      await fetchUser();
    }
  };

  const setTokenFromOAuth = (newToken) => {
    setToken(newToken);
  };

  const value = {
    user,
    loading,
    isAuthenticated: !!token && !!user,
    login,
    register,
    logout,
    refreshUser,
    setTokenFromOAuth
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

