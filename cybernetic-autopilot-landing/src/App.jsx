/**
 * Purpose: Main application coordinator.
 * Dependencies: React, components (Header, Hero, ConsoleWidget, SecurityGuardWidget, SpecsSection, InstallSection)
 * Role: Sets up structural sections and layout wrappers.
 */

import React, { useState } from 'react';
import Header from './components/Header.jsx';
import Hero from './components/Hero.jsx';
import ConsoleWidget from './components/ConsoleWidget.jsx';
import SecurityGuardWidget from './components/SecurityGuardWidget.jsx';
import SpecsSection from './components/SpecsSection.jsx';
import InstallSection from './components/InstallSection.jsx';

export default function App() {
  const [warningActive, setWarningActive] = useState(false);

  return (
    <div 
      className={`app-container ${warningActive ? 'warning-flash-active' : ''}`}
      style={{ 
        position: 'relative',
        minHeight: '100vh',
        zIndex: 2,
        transition: 'box-shadow 0.3s ease, border-color 0.3s ease',
        border: warningActive ? '4px solid var(--warning-color)' : '4px solid transparent',
      }}
    >
      <Header />
      
      <main style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 1.5rem' }}>
        <Hero />
        
        {/* Simulator Grid */}
        <section id="demo" style={{ padding: '4rem 0' }}>
          <h2 style={{ fontSize: '2.5rem', textAlign: 'center', marginBottom: '1rem' }}>
            Experience Autopilot Live
          </h2>
          <p style={{ color: 'var(--text-secondary)', textAlign: 'center', marginBottom: '3rem', maxWidth: '600px', margin: '0 auto 3rem auto' }}>
            Interact with the page itself using our simulated keyboard and speech-enabled commands, or trigger a safety validation.
          </p>
          <div style={{ 
            display: 'grid', 
            gridTemplateColumns: '1fr', 
            gap: '2.5rem',
            alignItems: 'stretch'
          }}>
            <ConsoleWidget />
            <SecurityGuardWidget onTriggerWarning={setWarningActive} />
          </div>
        </section>

        <SpecsSection />
        <InstallSection />
      </main>

      {/* Footer */}
      <footer style={{ 
        borderTop: '1px solid var(--card-border)', 
        padding: '3rem 1.5rem', 
        marginTop: '6rem', 
        textAlign: 'center', 
        color: 'var(--text-secondary)',
        fontSize: '0.9rem'
      }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1.5rem' }}>
          <div>
            <strong>VoiceUse</strong> — Open Source under MIT License.
          </div>
          <div style={{ display: 'flex', gap: '1.5rem' }}>
            <a href="#how-it-works" style={{ hover: 'color: var(--accent-color)' }}>How it Works</a>
            <a href="#specs" style={{ hover: 'color: var(--accent-color)' }}>Specs</a>
            <a href="#install" style={{ hover: 'color: var(--accent-color)' }}>Install</a>
            <a href="https://github.com" target="_blank" rel="noopener noreferrer">GitHub</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
