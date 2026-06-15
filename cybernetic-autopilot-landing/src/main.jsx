/**
 * Purpose: Entry point for React application.
 * Dependencies: react, react-dom, App.jsx, index.css
 * Role: Mounts the main React application to the DOM root.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
