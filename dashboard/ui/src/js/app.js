/**
 * Rooster Dashboard — Alpine.js app entry point.
 * In dev mode (Vite), this is loaded as a module.
 * In production, it's inlined into dashboard.html.
 *
 * This file re-exports the full app() function from the existing
 * dashboard.html for backward compatibility. The actual migration
 * happens incrementally by moving Alpine logic here.
 */

// Re-export i18n helper globally so Alpine templates can use t()
import { t, en, zh } from './i18n.js';

window.__rooster_lang = localStorage.getItem('rooster_lang') || 'zh';
window.__rooster_t = t;
window.__rooster_i18n = { en, zh };

// The full app() is still defined inline in dashboard.html.
// This module serves as the Vite HMR entry point for dev mode.
// Once the full migration is complete, app() will be defined here.

console.log(`[Rooster Dashboard] Dev mode active. Lang: ${window.__rooster_lang}`);
