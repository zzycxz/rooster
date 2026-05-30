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

// Customize marked renderer for premium UI
const renderer = new marked.Renderer();

renderer.code = function({text, lang}) {
  const language = lang || 'plaintext';
  let highlighted = text;
  if (lang && hljs.getLanguage(lang)) {
    try { highlighted = hljs.highlight(text, { language: lang }).value; } catch (_) {}
  } else {
    highlighted = hljs.highlightAuto(text).value;
  }
  
  const encodedCode = encodeURIComponent(text);
  
  return `
<div class="code-block-wrapper my-5 rounded-xl shadow-2xl border border-slate-700/60 bg-[#0d1117] overflow-hidden flex flex-col">
  <div class="flex items-center justify-between px-4 py-2.5 bg-slate-800/80 border-b border-slate-700/50">
    <div class="flex items-center gap-3">
      <div class="flex gap-1.5">
        <div class="w-3 h-3 rounded-full bg-[#ff5f56] shadow-sm"></div>
        <div class="w-3 h-3 rounded-full bg-[#ffbd2e] shadow-sm"></div>
        <div class="w-3 h-3 rounded-full bg-[#27c93f] shadow-sm"></div>
      </div>
      <span class="text-xs font-mono font-medium text-slate-400 opacity-80 uppercase tracking-wider">${language}</span>
    </div>
    <button class="copy-btn text-xs text-slate-400 hover:text-white transition-colors flex items-center gap-1 cursor-pointer" data-code="${encodedCode}">
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg> 
      <span>Copy</span>
    </button>
  </div>
  <div class="p-4 overflow-x-auto text-sm leading-relaxed font-mono">
    <pre style="margin:0;padding:0;background:transparent;"><code class="language-${language}">${highlighted}</code></pre>
  </div>
</div>`;
};

renderer.link = function({href, title, text}) {
  if (text === href || text.startsWith('http')) {
    return `
<a href="${href}" target="_blank" class="block my-3 no-underline group not-prose">
  <div class="flex items-center gap-3 p-3 rounded-xl border border-slate-700/50 bg-slate-800/40 hover:bg-slate-800/80 transition-all duration-300 shadow-sm hover:shadow-md">
    <div class="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400 group-hover:bg-blue-500/20 group-hover:scale-105 transition-all">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"></path></svg>
    </div>
    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium text-slate-200 truncate group-hover:text-blue-400 transition-colors">${href}</div>
      <div class="text-xs text-slate-500 truncate mt-0.5">${title || '点击访问链接'}</div>
    </div>
  </div>
</a>`;
  }
  return `<a href="${href}" title="${title || ''}" target="_blank">${text}</a>`;
};

marked.use({ renderer });

// Add global copy listener for the copy buttons
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.copy-btn');
  if (btn) {
    const code = decodeURIComponent(btn.getAttribute('data-code') || '');
    navigator.clipboard.writeText(code).then(() => {
      const originalHTML = btn.innerHTML;
      btn.innerHTML = '<span class="text-green-400 font-medium">✓ Copied</span>';
      setTimeout(() => { btn.innerHTML = originalHTML; }, 2000);
    });
  }
});

// Configure DOMPurify to allow SVG, paths for icons, and specific classes
DOMPurify.addHook('afterSanitizeAttributes', function (node) {
  if (node.tagName === 'svg' || node.tagName === 'path') {
    node.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  }
});

// Start Alpine after all globals are set
Alpine.start();
