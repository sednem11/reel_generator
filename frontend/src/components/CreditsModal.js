import React, { useState } from 'react';
import axios from 'axios';
import { useAuth } from '../contexts/AuthContext';
import './CreditsModal.css';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const PAYMENT_PLANS = [
  { credits: 60, price: 5.99, currency: 'EUR', popular: false },
  { credits: 200, price: 14.99, currency: 'EUR', popular: true },
  { credits: 700, price: 49.99, currency: 'EUR', popular: false },
];

function CreditsModal({ isOpen, onClose }) {
  const { refreshUser } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedPlan, setSelectedPlan] = useState(null);

  if (!isOpen) return null;

  const handlePurchase = async (plan) => {
    setLoading(true);
    setError(null);
    setSelectedPlan(plan);

    try {
      const response = await axios.post(
        `${API_BASE_URL}/api/payment/purchase`,
        {
          credits: plan.credits,
          amount: plan.price,
          currency: plan.currency,
        },
        {
          headers: {
            Authorization: `Bearer ${localStorage.getItem('token')}`,
          },
        }
      );

      if (response.data.success) {
        // Refresh user data to update credits
        await refreshUser();
        alert(`Successfully purchased ${plan.credits} credits!`);
        onClose();
      } else {
        setError(response.data.message || 'Payment failed');
      }
    } catch (err) {
      console.error('Payment error:', err);
      setError(
        err.response?.data?.detail || 
        err.message || 
        'Payment failed. Please try again.'
      );
    } finally {
      setLoading(false);
      setSelectedPlan(null);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="credits-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Buy Credits</h2>
          <button className="close-button" onClick={onClose}>&times;</button>
        </div>

        <div className="modal-content">
          <p className="modal-description">
            Choose a credit package to continue creating amazing reels!
          </p>

          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          <div className="payment-plans">
            {PAYMENT_PLANS.map((plan, index) => (
              <div
                key={index}
                className={`payment-plan ${plan.popular ? 'popular' : ''} ${
                  selectedPlan?.credits === plan.credits && loading ? 'processing' : ''
                }`}
              >
                {plan.popular && (
                  <div className="popular-badge">Most Popular</div>
                )}
                <div className="plan-header">
                  <h3>{plan.credits} Credits</h3>
                  <div className="plan-price">
                    <span className="price-amount">€{plan.price}</span>
                    <span className="price-per-credit">
                      €{(plan.price / plan.credits).toFixed(3)} per credit
                    </span>
                  </div>
                </div>
                <div className="plan-features">
                  <div className="feature-item">
                    <span className="feature-icon">✓</span>
                    <span>{plan.credits} video processing credits</span>
                  </div>
                  <div className="feature-item">
                    <span className="feature-icon">✓</span>
                    <span>No expiration date</span>
                  </div>
                  <div className="feature-item">
                    <span className="feature-icon">✓</span>
                    <span>Instant activation</span>
                  </div>
                </div>
                <button
                  className="purchase-button"
                  onClick={() => handlePurchase(plan)}
                  disabled={loading}
                >
                  {loading && selectedPlan?.credits === plan.credits ? (
                    <span>Processing...</span>
                  ) : (
                    `Buy for €${plan.price}`
                  )}
                </button>
              </div>
            ))}
          </div>

          <div className="modal-footer">
            <p className="footer-note">
              💳 Secure payment processing. Credits are added instantly after purchase.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default CreditsModal;

