/**
 * Purpose: Global navigation and header layout.
 * Dependencies: React, lucide-react (Menu, X, Code, Sun, Moon)
 * Role: Provides navigation landmarks, the theme switcher toggle, and toggles mobile view.
 */

import React, { useState, useEffect } from 'react';
import { Menu, X, Code, Sun, Moon } from 'lucide-react';

export default function Header() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [theme, setTheme] = useState(() => {
    return document.documentElement.classList.contains('dark-theme') ? 'dark' : 'light';
  });

  /**
   * Toggles the light/dark theme by swapping classes on document.documentElement.
   * 
   * Extended: Swaps classes between dark-theme and light-theme. Saves preference to localStorage.
   * 
   * @returns {void}
   */
  const toggleTheme = () => {
    const nextTheme = theme === 'dark' ? 'light' : 'dark';
    if (nextTheme === 'dark') {
      document.documentElement.classList.add('dark-theme');
      document.documentElement.classList.remove('light-theme');
    } else {
      document.documentElement.classList.add('light-theme');
      document.documentElement.classList.remove('dark-theme');
    }
    setTheme(nextTheme);
    localStorage.setItem('theme', nextTheme);
  };

  /**
   * Opens or closes the mobile-screen slideout menu.
   * 
   * @returns {void}
   */
  const toggleMobileMenu = () => {
    setMobileMenuOpen(!mobileMenuOpen);
  };

  return (
    <header style={{
      position: 'sticky',
      top: 0,
      backdropFilter: 'blur(16px)',
      background: 'rgba(var(--bg-rgb), 0.7)',
      borderBottom: '1px solid var(--card-border)',
      zIndex: 100,
      transition: 'background var(--transition-normal)'
    }}>
      <div style={{
        maxWidth: '1200px',
        margin: '0 auto',
        padding: '1rem 1.5rem',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        {/* Logo */}
        <a href="#" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 700, fontFamily: 'var(--font-display)', fontSize: '1.25rem' }}>
          <div style={{
            background: 'linear-gradient(135deg, var(--accent-color), #a24bfb)',
            padding: '0.5rem',
            borderRadius: '12px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            boxShadow: '0 0 10px var(--accent-glow)'
          }}>
            <Code size={20} />
          </div>
          <span>Voice<span style={{ color: 'var(--accent-color)' }}>Use</span></span>
        </a>

        {/* Desktop Navigation */}
        <nav style={{ display: 'flex', alignItems: 'center', gap: '2rem' }} className="desktop-nav">
          <a href="#how-it-works" style={{ fontSize: '0.95rem', fontWeight: 500, color: 'var(--text-secondary)' }} onMouseEnter={(e) => e.target.style.color = 'var(--text-primary)'} onMouseLeave={(e) => e.target.style.color = 'var(--text-secondary)'}>How it Works</a>
          <a href="#specs" style={{ fontSize: '0.95rem', fontWeight: 500, color: 'var(--text-secondary)' }} onMouseEnter={(e) => e.target.style.color = 'var(--text-primary)'} onMouseLeave={(e) => e.target.style.color = 'var(--text-secondary)'}>Specs</a>
          <a href="#install" style={{ fontSize: '0.95rem', fontWeight: 500, color: 'var(--text-secondary)' }} onMouseEnter={(e) => e.target.style.color = 'var(--text-primary)'} onMouseLeave={(e) => e.target.style.color = 'var(--text-secondary)'}>Install</a>
          
          {/* Theme Toggle Button */}
          <button 
            onClick={toggleTheme} 
            style={{ 
              background: 'none', 
              border: 'none', 
              color: 'var(--text-primary)', 
              cursor: 'pointer', 
              padding: '0.5rem',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
            title="Toggle theme"
          >
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          </button>

          <a 
            href="#install" 
            className="btn btn-primary"
            style={{ padding: '0.5rem 1.25rem', fontSize: '0.9rem' }}
          >
            Get Code
          </a>
        </nav>

        {/* Mobile Menu Actions */}
        <div style={{ display: 'none' }} className="mobile-nav-toggle-container">
          <button 
            onClick={toggleTheme} 
            style={{ 
              background: 'none', 
              border: 'none', 
              color: 'var(--text-primary)', 
              cursor: 'pointer', 
              marginRight: '1rem' 
            }}
          >
            {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
          </button>
          <button 
            onClick={toggleMobileMenu} 
            style={{ 
              background: 'none', 
              border: 'none', 
              color: 'var(--text-primary)', 
              cursor: 'pointer' 
            }}
          >
            {mobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
        </div>
      </div>

      {/* CSS style hook to handle responsive rendering without styled-components */}
      <style>{`
        @media (max-width: 768px) {
          .desktop-nav {
            display: none !important;
          }
          .mobile-nav-toggle-container {
            display: flex !important;
            align-items: center;
          }
        }
      `}</style>

      {/* Mobile Menu Drawer */}
      {mobileMenuOpen && (
        <div style={{
          position: 'fixed',
          top: '60px',
          left: 0,
          width: '100%',
          height: 'calc(100vh - 60px)',
          background: 'var(--bg-color)',
          zIndex: 99,
          padding: '2rem 1.5rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '2rem',
          borderTop: '1px solid var(--card-border)'
        }}>
          <a href="#how-it-works" onClick={toggleMobileMenu} style={{ fontSize: '1.25rem', fontWeight: 600 }}>How it Works</a>
          <a href="#specs" onClick={toggleMobileMenu} style={{ fontSize: '1.25rem', fontWeight: 600 }}>Specs</a>
          <a href="#install" onClick={toggleMobileMenu} style={{ fontSize: '1.25rem', fontWeight: 600 }}>Install</a>
          <a 
            href="#install" 
            onClick={toggleMobileMenu}
            className="btn btn-primary"
            style={{ width: '100%', padding: '1rem' }}
          >
            Get Code
          </a>
        </div>
      )}
    </header>
  );
}
