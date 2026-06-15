/**
 * Purpose: Hero section showcasing title, CTA, and lazy-loaded/perf-aware video demo.
 * Dependencies: React, lucide-react (Play, Pause, ChevronRight)
 * Role: Acts as LCP component; handles intersection-based video playback and reduced-motion compliance.
 */

import React, { useRef, useEffect, useState } from 'react';
import { Play, Pause, ChevronRight } from 'lucide-react';

export default function Hero() {
  const videoRef = useRef(null);
  const containerRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isIntersecting, setIsIntersecting] = useState(false);

  /**
   * Toggles video playback manually.
   * 
   * @returns {void}
   */
  const handlePlayToggle = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
      setIsPlaying(false);
    } else {
      videoRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(err => console.log("Video play interrupted:", err));
    }
  };

  useEffect(() => {
    // Check system preferences for reduced motion
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    
    // Intersection Observer to control autoplay and resource saving
    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsIntersecting(entry.isIntersecting);
        if (!videoRef.current) return;

        if (entry.isIntersecting && !prefersReducedMotion) {
          // Play video when visible
          videoRef.current.play()
            .then(() => setIsPlaying(true))
            .catch(() => setIsPlaying(false));
        } else {
          // Pause video when out of viewport to conserve CPU/GPU resources
          videoRef.current.pause();
          setIsPlaying(false);
        }
      },
      { threshold: 0.25 }
    );

    if (containerRef.current) {
      observer.observe(containerRef.current);
    }

    return () => {
      observer.disconnect();
    };
  }, []);

  return (
    <section 
      ref={containerRef}
      style={{
        padding: '6rem 0 4rem 0',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        textAlign: 'center',
        position: 'relative'
      }}
    >
      {/* Decorative Glow behind text */}
      <div style={{
        position: 'absolute',
        top: '10%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        width: '500px',
        height: '250px',
        background: 'radial-gradient(circle, rgba(138, 43, 226, 0.15) 0%, transparent 70%)',
        zIndex: -1,
        pointerEvents: 'none'
      }} />

      {/* Hero Display Headings */}
      <h1 style={{
        fontSize: 'clamp(2.5rem, 5vw, 4.5rem)',
        lineHeight: 1.05,
        fontFamily: 'var(--font-display)',
        textTransform: 'uppercase',
        maxWidth: '900px',
        marginBottom: '1.5rem',
        background: 'linear-gradient(to right, var(--text-primary), var(--accent-secondary), var(--text-primary))',
        WebkitBackgroundClip: 'text',
        WebkitTextFillColor: 'transparent',
        animation: 'glow 10s infinite alternate'
      }}>
        Operate Your System <br />
        With A Whisper.
      </h1>

      <p style={{
        fontSize: 'clamp(1.1rem, 2vw, 1.35rem)',
        color: 'var(--text-secondary)',
        maxWidth: '650px',
        marginBottom: '2.5rem',
        fontWeight: 400
      }}>
        An open-source, local desktop voice companion that controls your OS hands-free. Realtime STT, safe execution boundaries, and extensible plugin actions.
      </p>

      {/* CTA Button Actions */}
      <div style={{
        display: 'flex',
        gap: '1rem',
        marginBottom: '4.5rem',
        flexWrap: 'wrap',
        justifyContent: 'center'
      }}>
        <a href="#demo" className="btn btn-primary" style={{ gap: '0.5rem' }}>
          Try Web Demo <ChevronRight size={18} />
        </a>
        <a href="#install" className="btn btn-secondary">
          Install Locally
        </a>
      </div>

      {/* Video Loop Widget */}
      <div style={{
        position: 'relative',
        width: '100%',
        maxWidth: '850px',
        borderRadius: '24px',
        border: '1px solid var(--card-border)',
        background: 'var(--card-bg)',
        padding: '8px',
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5), 0 0 40px rgba(138, 43, 226, 0.1)',
        backdropFilter: 'blur(20px)',
        overflow: 'hidden'
      }}>
        {/* Inner video wrapper */}
        <div style={{
          position: 'relative',
          borderRadius: '16px',
          overflow: 'hidden',
          aspectRatio: '16/9',
          background: '#04020a'
        }}>
          {/* Simulated looping webm/mp4 overlay */}
          <video
            ref={videoRef}
            src="https://assets.mixkit.co/videos/preview/mixkit-working-at-a-computer-terminal-in-neon-lighting-34329-large.mp4"
            loop
            muted
            playsInline
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              opacity: isIntersecting ? 0.8 : 0.2,
              transition: 'opacity var(--transition-slow)'
            }}
          />

          {/* Abstract Cyber Overlay */}
          <div style={{
            position: 'absolute',
            inset: 0,
            background: 'linear-gradient(to top, rgba(11, 6, 24, 0.8) 0%, transparent 60%)',
            pointerEvents: 'none'
          }} />

          {/* Interactive controls */}
          <div style={{
            position: 'absolute',
            bottom: '1.5rem',
            left: '1.5rem',
            display: 'flex',
            alignItems: 'center',
            gap: '1rem',
            zIndex: 10
          }}>
            <button 
              onClick={handlePlayToggle}
              style={{
                background: 'rgba(138, 43, 226, 0.8)',
                color: '#fff',
                border: 'none',
                width: '48px',
                height: '48px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                boxShadow: '0 0 15px var(--accent-glow)',
                transition: 'transform var(--transition-fast)'
              }}
              onMouseEnter={(e) => e.currentTarget.style.transform = 'scale(1.08)'}
              onMouseLeave={(e) => e.currentTarget.style.transform = 'scale(1)'}
            >
              {isPlaying ? <Pause size={20} /> : <Play size={20} style={{ marginLeft: '2px' }} />}
            </button>
            <div style={{ textAlign: 'left' }}>
              <p style={{ color: '#fff', fontWeight: 600, fontSize: '0.9rem', margin: 0 }}>Hands-free Autopilot Demo</p>
              <p style={{ color: 'rgba(255,255,255,0.6)', fontSize: '0.75rem', margin: 0 }}>
                {isPlaying ? 'Running voice commands...' : 'Demo Paused'}
              </p>
            </div>
          </div>

          {/* Code Execution Overlay Watermark */}
          <div style={{
            position: 'absolute',
            top: '1.5rem',
            right: '1.5rem',
            fontFamily: 'monospace',
            background: 'rgba(11, 6, 24, 0.75)',
            border: '1px solid rgba(138, 43, 226, 0.3)',
            borderRadius: '8px',
            padding: '0.5rem 0.75rem',
            fontSize: '0.8rem',
            color: 'var(--accent-secondary)',
            backdropFilter: 'blur(8px)',
            pointerEvents: 'none'
          }}>
            VoiceUse: active_session_01
          </div>
        </div>
      </div>
    </section>
  );
}
