(function () {
  'use strict';

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const catalogueSearchTerm = (value) => {
    if (!/\b(book|books|title|titles|catalog|catalogue|isbn|issn|call\s*number|available|availability|do\s+you\s+have|are\s+there|find|show\s+me)\b/i.test(value)) {
      return '';
    }
    let cleaned = String(value);
    [
      /\b(?:please\s+)?(?:do\s+you\s+have|are\s+there|can\s+i\s+find|show\s+me|find\s+me|find|search\s+for|i\s+need|i\s+want)\b/gi,
      /\b(?:any|some)\b/gi,
      /\b(?:books?|titles?|resources?|materials?|catalog(?:ue)?(?:\s+records?)?)\b/gi,
      /\b(?:related\s+to|related|about|on)\b/gi,
      /\b(?:in\s+the\s+library|from\s+the\s+library)\b/gi
    ].forEach((pattern) => { cleaned = cleaned.replace(pattern, ' '); });
    cleaned = cleaned.replace(/[^\p{L}\p{N}_\-\s]/gu, ' ').replace(/\s+/g, ' ').trim();
    return cleaned.length >= 2 ? cleaned : '';
  };

  const setHiddenValue = (form, name, value) => {
    let input = form.querySelector('input[type="hidden"][name="' + name + '"]');
    if (!input) {
      input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      form.appendChild(input);
    }
    input.value = value;
  };

  const initSmartSearchForms = () => {
    document.querySelectorAll('form').forEach((form) => {
      if (!form.querySelector('input[name="lookfor"]')) return;
      form.addEventListener('submit', () => {
        const lookfor = form.querySelector('input[name="lookfor"]:not([disabled])');
        if (!lookfor) return;
        const aiField = form.querySelector('[name="ai"]');
        const aiEnabled = aiField
          ? aiField.value !== '0'
          : new URLSearchParams(window.location.search).get('ai') !== '0';
        if (!aiEnabled) return;
        const original = lookfor.value.trim();
        const cleaned = catalogueSearchTerm(original);
        if (!cleaned || cleaned.toLocaleLowerCase() === original.toLocaleLowerCase()) return;
        setHiddenValue(form, 'ai_question', original);
        setHiddenValue(form, 'ai', '1');
        lookfor.value = cleaned;
      });
    });
  };

  const initSearchClearButtons = () => {
    document.querySelectorAll('[data-kdai-clear-search]').forEach((button) => {
      button.addEventListener('click', () => {
        const form = button.closest('form');
        const input = form && form.querySelector('input[name="lookfor"]');
        if (input) {
          input.value = '';
          input.focus();
        }
      });
    });
  };

  const initMobileMenus = () => {
    document.querySelectorAll('[data-kdai-menu-toggle]').forEach((button) => {
      const nav = document.getElementById(button.getAttribute('aria-controls'));
      if (!nav) return;
      button.addEventListener('click', () => {
        const open = button.getAttribute('aria-expanded') !== 'true';
        button.setAttribute('aria-expanded', String(open));
        button.setAttribute('aria-label', open ? 'Close navigation menu' : 'Open navigation menu');
        nav.classList.toggle('kdai-nav-open', open);
      });
    });
  };

  const initHomepage = () => {
    const root = document.querySelector('[data-kdai-home]');
    if (!root) return;
    document.documentElement.classList.add('kdai-theme');
    document.body.classList.add('kdai-homepage');

    const base = root.dataset.basePath || '';
    const form = root.querySelector('[data-kdai-main-search]');
    const aiValue = root.querySelector('[data-kdai-ai-value]');
    const aiToggle = root.querySelector('[data-kdai-ai-toggle]');
    const standardQuery = root.querySelector('[data-kdai-standard-query]');
    const aiQuery = root.querySelector('[data-kdai-ai-query]');
    const providerValue = root.querySelector('[data-kdai-provider-value]');
    const providerSelect = root.querySelector('[data-kdai-provider-select]');
    const providerChoices = root.querySelectorAll('input[name="kdai-provider-choice"]');

    const syncProvider = () => {
      const selected = root.querySelector('input[name="kdai-provider-choice"]:checked');
      const provider = selected ? selected.value : 'openai';
      if (providerValue) providerValue.value = provider;

      const url = new URL(window.location.href);
      url.searchParams.set('ai_provider', provider);
      window.history.replaceState({}, '', url);
    };

    providerChoices.forEach(choice => {
      choice.addEventListener('change', syncProvider);
    });
    syncProvider();

    if (aiToggle) {
      aiToggle.addEventListener('change', () => {
        const ai = aiToggle.checked;
        if (aiValue) aiValue.value = ai ? '1' : '0';
        if (providerSelect) providerSelect.hidden = !ai;

        const source = ai ? standardQuery : aiQuery;
        const target = ai ? aiQuery : standardQuery;
        if (source && target) {
          target.value = source.value;
          source.hidden = true;
          source.disabled = true;
          target.hidden = false;
          target.disabled = false;
          target.focus();
        }

        const url = new URL(window.location.href);
        url.searchParams.set('ai', ai ? '1' : '0');
        window.history.replaceState({}, '', url);
      });
    }

    const grid = root.querySelector('[data-kdai-resources]');
    const status = root.querySelector('[data-kdai-status]');
    const endpoint = base + '/api/v1/search';
    const apiSearch = async (params) => {
      const query = new URLSearchParams(params);
      const response = await fetch(endpoint + '?' + query.toString(), {
        headers: {Accept: 'application/json'},
        credentials: 'same-origin'
      });
      if (!response.ok) throw new Error('Search API returned HTTP ' + response.status);
      return response.json();
    };
    const setCount = (name, value) => {
      const node = root.querySelector('[data-kdai-count="' + name + '"]');
      if (node && Number.isFinite(value)) {
        node.textContent = new Intl.NumberFormat().format(Math.max(0, value));
      }
    };
    const firstAuthor = (record) => {
      const groups = record.authors || {};
      for (const key of ['primary', 'secondary', 'corporate']) {
        const value = groups[key];
        if (Array.isArray(value) && value.length) return String(value[0]);
        if (value && typeof value === 'object') {
          const names = Object.keys(value);
          if (names.length) return names[0];
        }
      }
      return 'Library collection';
    };
    const buildCard = (record) => {
      const id = String(record.id || '');
      const dspace = id.startsWith('dspace-');
      const article = make('article', 'kdai-resource-card');
      const cover = make('div', 'kdai-resource-cover');
      const img = document.createElement('img');
      img.alt = '';
      img.loading = 'lazy';
      const isbn = Array.isArray(record.isbns) && record.isbns.length ? String(record.isbns[0]) : '';
      img.src = dspace
        ? base + '/Cover/Show?recordid=' + encodeURIComponent(id) + '&source=Solr&size=medium'
        : base + '/Cover/Show?isbn=' + encodeURIComponent(isbn) + '&size=medium';
      img.addEventListener('error', () => {
        img.remove();
        cover.appendChild(make('span', '', dspace ? '▧' : '▤'));
      }, {once: true});
      cover.appendChild(img);
      const copy = make('div', 'kdai-resource-copy');
      copy.appendChild(make(
        'span',
        'kdai-source-badge ' + (dspace ? 'kdai-source-dspace' : 'kdai-source-koha'),
        dspace ? 'DSpace · Open access' : 'Koha · Book'
      ));
      const h3 = make('h3');
      const link = make('a', '', record.title || 'Untitled resource');
      link.href = base + '/Record/' + encodeURIComponent(id);
      h3.appendChild(link);
      copy.appendChild(h3);
      copy.appendChild(make('p', '', firstAuthor(record)));
      const formats = Array.isArray(record.formats) && record.formats.length
        ? record.formats.join(' · ')
        : (dspace ? 'Research document' : 'Library resource');
      copy.appendChild(make('p', 'kdai-format', formats));
      article.append(cover, copy);
      return article;
    };

    if (!grid || !status) return;
    Promise.all([
      apiSearch({lookfor: '*', type: 'AllFields', page: '1', limit: '4'}),
      apiSearch({lookfor: '*', type: 'AllFields', page: '1', limit: '1', 'filter[]': 'collection:"DSpace"'})
    ]).then(([all, dspace]) => {
      const allCount = Number(all.resultCount || 0);
      const dspaceCount = Number(dspace.resultCount || 0);
      setCount('all', allCount);
      setCount('dspace', dspaceCount);
      setCount('koha', allCount - dspaceCount);
      const cards = (all.records || []).slice(0, 4).map(buildCard);
      if (!cards.length) throw new Error('No live records returned');
      grid.replaceChildren(...cards);
      grid.setAttribute('aria-busy', 'false');
      status.textContent = 'Live records from the unified Koha and DSpace index.';
    }).catch((error) => {
      grid.replaceChildren(make('p', 'kdai-live-error', 'Live resource cards are temporarily unavailable. Search remains fully operational.'));
      grid.setAttribute('aria-busy', 'false');
      status.textContent = error.message;
    });
  };

  const initAiResults = () => {
    const panel = document.querySelector('[data-kdai-ai-panel]');
    if (!panel) return;
    const query = (panel.dataset.query || '').trim();
    const base = (panel.dataset.basePath || '/vufind').replace(/\/$/, '');
    const enabled = new URLSearchParams(window.location.search).get('ai') !== '0';
    if (!enabled || query.length < 3 || query === '*') {
      panel.hidden = true;
      return;
    }

    const loading = panel.querySelector('[data-kdai-ai-loading]');
    const responseBox = panel.querySelector('[data-kdai-ai-response]');
    const answerBox = panel.querySelector('[data-kdai-ai-answer]');
    const sourcesBox = panel.querySelector('[data-kdai-sources]');
    const errorBox = panel.querySelector('[data-kdai-ai-error]');
    const state = panel.querySelector('[data-kdai-ai-state]');
    const model = panel.querySelector('[data-kdai-model]');
    const retry = panel.querySelector('[data-kdai-ai-retry]');
    const copy = panel.querySelector('[data-kdai-copy-answer]');
    const toggleSources = panel.querySelector('[data-kdai-toggle-sources]');
    let latestAnswer = '';

    const addAnswerText = (text, sources) => {
      answerBox.replaceChildren();
      const validNumbers = new Set(sources.map((source) => Number(source.number)));
      String(text).split(/\n\s*\n/).filter(Boolean).forEach((paragraph) => {
        const p = make('p');
        paragraph.split(/(\[\d+\])/g).filter(Boolean).forEach((part) => {
          const match = part.match(/^\[(\d+)\]$/);
          const number = match ? Number(match[1]) : null;
          if (number && validNumbers.has(number)) {
            const citation = make('a', 'kdai-citation', part);
            citation.href = '#kdai-source-' + number;
            citation.setAttribute('aria-label', 'View supporting source ' + number);
            p.appendChild(citation);
          } else {
            p.appendChild(document.createTextNode(part));
          }
        });
        answerBox.appendChild(p);
      });
    };

    const sourceCard = (source) => {
        const article = make('article');
        article.id = 'kdai-source-' + source.number;
        article.className = source.cited ? 'kdai-source-cited' : 'kdai-source-related';
        const visual = make('div', 'kdai-evidence-cover');
        if (String(source.source).toUpperCase() === 'KOHA') {
          const img = document.createElement('img');
          img.alt = 'Book cover for ' + source.title;
          img.loading = 'lazy';
          const coverParams = source.isbn
            ? 'isbn=' + encodeURIComponent(source.isbn)
            : 'recordid=' + encodeURIComponent(source.vufind_id || '') + '&source=Solr';
          img.src = base + '/Cover/Show?' + coverParams + '&size=medium';
          img.addEventListener('error', () => {
            visual.replaceChildren(make('span', '', '▤'));
          }, {once: true});
          visual.appendChild(img);
        } else {
          visual.classList.add('kdai-evidence-document');
          visual.appendChild(make('span', '', '▧'));
        }
        const details = make('div', 'kdai-evidence-details');
        const status = source.cited ? 'Cited evidence' : 'Related source';
        const label = make('span', '', status + ' · ' + source.source + ' · ' + source.locator);
        const title = make('strong');
        const link = make('a', '', '[' + source.number + '] ' + source.title);
        link.href = source.record_url;
        title.appendChild(link);
        const note = make(
          'small',
          '',
          source.cited
            ? 'This source directly supports the generated answer.'
            : 'Semantically related library material; it was not used to support a claim in this answer.'
        );
        details.append(label, title, note);
        article.append(visual, details);
        return article;
    };

    const addSources = (sources) => {
      const cited = sources.filter((source) => source.cited === true);
      const related = sources.filter((source) => source.cited !== true);
      const groups = [];
      if (cited.length) {
        const section = make('section', 'kdai-source-group');
        section.appendChild(make('h3', '', 'Cited evidence'));
        const grid = make('div', 'kdai-source-cards');
        grid.append(...cited.map(sourceCard));
        section.appendChild(grid);
        groups.push(section);
      }
      if (related.length) {
        const section = make('section', 'kdai-source-group');
        section.appendChild(make('h3', '', 'Related library sources'));
        section.appendChild(make('p', 'kdai-source-explanation', 'These records are relevant to the question, but the answer does not claim that their unavailable full text proves its statements.'));
        const grid = make('div', 'kdai-source-cards');
        grid.append(...related.map(sourceCard));
        section.appendChild(grid);
        groups.push(section);
      }
      sourcesBox.replaceChildren(...groups);
    };

    const load = async () => {
      loading.hidden = false;
      responseBox.hidden = true;
      errorBox.hidden = true;
      state.textContent = '● SEARCHING';
      state.classList.add('kdai-state-loading');
      const urlProvider = new URL(window.location.href).searchParams.get('ai_provider');
      const selectedProvider = urlProvider === 'ollama' ? 'ollama' : 'openai';
      const controller = new AbortController();
      const timeoutMs = selectedProvider === 'ollama' ? 330000 : 120000;
      const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(panel.dataset.endpoint || '/kdai-ai/answer', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
          body: JSON.stringify({query: query, provider: selectedProvider}),
          signal: controller.signal
        });
        let data = null;
        try { data = await response.json(); } catch (ignore) { data = null; }
        if (!response.ok || !data || data.status !== 'OK') {
          throw new Error((data && data.detail) || 'AI service returned HTTP ' + response.status);
        }
        latestAnswer = String(data.answer || '');
        const sources = Array.isArray(data.sources) ? data.sources : [];
        addAnswerText(latestAnswer, sources);
        addSources(sources);
        const timing = data.usage && data.usage.cached
          ? ' · cached'
          : (data.provider === 'catalogue' ? ' · live catalogue' : ' · generated now');
        model.textContent = (data.model || 'AI') + timing;
        loading.hidden = true;
        responseBox.hidden = false;
        state.textContent = '✓ EVIDENCE AVAILABLE';
        state.classList.remove('kdai-state-loading');
      } catch (error) {
        loading.hidden = true;
        errorBox.hidden = false;
        state.textContent = 'AI UNAVAILABLE';
        state.classList.remove('kdai-state-loading');
        console.error('KDAI answer request failed:', error);
      } finally {
        window.clearTimeout(timeout);
      }
    };

    retry.addEventListener('click', load);
    copy.addEventListener('click', async () => {
      if (!latestAnswer) return;
      try {
        await navigator.clipboard.writeText(latestAnswer);
        copy.textContent = '✓ Answer copied';
        window.setTimeout(() => { copy.textContent = '□ Copy answer'; }, 1800);
      } catch (error) {
        copy.textContent = 'Copy unavailable';
      }
    });
    toggleSources.addEventListener('click', () => {
      const show = sourcesBox.hidden;
      sourcesBox.hidden = !show;
      toggleSources.setAttribute('aria-expanded', String(show));
    });
    load();
  };

  document.addEventListener('DOMContentLoaded', () => {
    initMobileMenus();
    initSmartSearchForms();
    initSearchClearButtons();
    initHomepage();
    initAiResults();
  });
}());

document.addEventListener('DOMContentLoaded', () => {
  const qrButton = document.querySelector('.kdai-qr-button');
  const qrModal = document.querySelector('[data-kdai-qr-modal]');
  const qrClose = document.querySelector('[data-kdai-qr-close]');
  const copyLink = document.querySelector('[data-kdai-copy-link]');

  if (!qrButton || !qrModal) return;

  const openQr = () => {
    qrModal.hidden = false;
    document.body.style.overflow = 'hidden';
    qrClose?.focus();
  };

  const closeQr = () => {
    qrModal.hidden = true;
    document.body.style.overflow = '';
    qrButton.focus();
  };

  qrButton.addEventListener('click', openQr);
  qrClose?.addEventListener('click', closeQr);

  qrModal.addEventListener('click', (event) => {
    if (event.target === qrModal) closeQr();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !qrModal.hidden) closeQr();
  });

  copyLink?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      const oldText = copyLink.textContent;
      copyLink.textContent = 'Copied';
      setTimeout(() => {
        copyLink.textContent = oldText;
      }, 1500);
    } catch {
      copyLink.textContent = 'Copy failed';
    }
  });
});
