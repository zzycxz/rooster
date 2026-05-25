/**
 * Dashboard entry point — imports local npm packages and exposes them globally.
 * This replaces CDN <script> tags with bundled local dependencies.
 */
import Alpine from 'alpinejs';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';

// Expose to global scope for inline Alpine.js templates
window.Alpine = Alpine;
window.marked = marked;
window.DOMPurify = DOMPurify;
window.hljs = hljs;

// Configure marked with highlight.js integration
marked.setOptions({
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang }).value; } catch (_) {}
    }
    return hljs.highlightAuto(code).value;
  },
});

// Start Alpine after all globals are set
Alpine.start();
