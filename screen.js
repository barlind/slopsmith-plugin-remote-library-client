(function() {
    const state = window.__remoteLibraryClientPlugin || {
        installed: false,
        sources: [],
        addOpen: false,
        loading: false,
        refreshing: false,
        adding: false,
        sourceBusy: {}
    };
    window.__remoteLibraryClientPlugin = state;
    if (typeof state.addOpen !== 'boolean') state.addOpen = false;
    if (typeof state.loading !== 'boolean') state.loading = false;
    if (typeof state.refreshing !== 'boolean') state.refreshing = false;
    if (typeof state.adding !== 'boolean') state.adding = false;
    if (!state.sourceBusy || typeof state.sourceBusy !== 'object') state.sourceBusy = {};

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function setMessage(message, tone) {
        const node = document.getElementById('remote-library-client-message');
        if (!node) return;
        node.textContent = message || '';
        node.className = `mt-3 text-sm ${tone === 'error' ? 'text-red-300' : tone === 'success' ? 'text-green-300' : 'text-gray-400'}`;
    }

    function setAddFormOpen(open, { focus = false } = {}) {
        state.addOpen = !!open;
        const form = document.getElementById('remote-library-client-add-form');
        const toggle = document.getElementById('rlc-toggle-add');
        if (form) form.classList.toggle('hidden', !state.addOpen);
        if (toggle) {
            toggle.setAttribute('aria-expanded', state.addOpen ? 'true' : 'false');
            toggle.textContent = state.addOpen ? 'x' : '+';
        }
        if (state.addOpen && focus) document.getElementById('rlc-base-url')?.focus();
    }

    function clearAddForm() {
        const baseUrl = document.getElementById('rlc-base-url');
        const label = document.getElementById('rlc-label');
        if (baseUrl) baseUrl.value = '';
        if (label) label.value = '';
    }

    function normalizeBaseUrl(value) {
        const raw = String(value || '').trim();
        if (!raw) return '';
        if (/^https?:\/\//i.test(raw)) return raw.replace(/\/+$/, '');
        return `http://${raw}`.replace(/\/+$/, '');
    }

    async function api(path, options) {
        const response = await fetch(`/api/plugins/remote_library_client${path}`, {
            headers: { 'Content-Type': 'application/json' },
            ...(options || {}),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
        return data;
    }

    async function refreshCoreLibraryProviders({ reloadOnChange = false } = {}) {
        if (typeof window.loadLibraryProviders === 'function') {
            await window.loadLibraryProviders({ restoreSaved: true, reloadOnChange });
        }
    }

    function setBusyState(next = {}) {
        if (typeof next.loading === 'boolean') state.loading = next.loading;
        if (typeof next.refreshing === 'boolean') state.refreshing = next.refreshing;
        if (typeof next.adding === 'boolean') state.adding = next.adding;
        syncActionButtons();
    }

    function setSourceBusy(providerId, mode = '') {
        if (!providerId) return;
        if (mode) state.sourceBusy[providerId] = mode;
        else delete state.sourceBusy[providerId];
        renderSources();
    }

    function syncActionButtons() {
        const refreshBtn = document.querySelector('[data-rlc-refresh]');
        const addBtn = document.querySelector('[data-rlc-form] button[type="submit"]');
        const canInteract = !(state.loading || state.refreshing || state.adding);
        if (refreshBtn) {
            refreshBtn.disabled = !canInteract;
            refreshBtn.textContent = state.loading || state.refreshing ? 'Refreshing...' : 'Refresh';
            refreshBtn.classList.toggle('opacity-60', !canInteract);
            refreshBtn.classList.toggle('cursor-not-allowed', !canInteract);
        }
        if (addBtn) {
            addBtn.disabled = !canInteract;
            addBtn.textContent = state.adding ? 'Adding...' : 'Add';
            addBtn.classList.toggle('opacity-60', !canInteract);
            addBtn.classList.toggle('cursor-not-allowed', !canInteract);
        }
    }

    function renderSources() {
        const node = document.getElementById('remote-library-client-sources');
        if (!node) return;
        if (state.loading && !state.sources.length) {
            node.innerHTML = '<div class="rounded-xl border border-gray-800/50 bg-dark-700/30 px-4 py-6 text-sm text-gray-400">Loading sources...</div>';
            return;
        }
        if (!state.sources.length) {
            node.innerHTML = '<div class="rounded-xl border border-gray-800/50 bg-dark-700/30 px-4 py-6 text-sm text-gray-400">No sources yet. Click + to add one.</div>';
            return;
        }
        node.innerHTML = state.sources.map(source => {
            const offline = !source.online;
            const busyMode = source.providerId ? (state.sourceBusy[source.providerId] || '') : '';
            const busy = !!busyMode;
            return `
            <div class="rounded-xl border border-gray-800/50 bg-dark-700/50 p-4 transition hover:border-accent/20">
                <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div class="min-w-0">
                        <div class="truncate text-sm font-semibold text-white">${esc(source.label || source.sourceName || source.baseUrl)}</div>
                        <div class="mt-1 truncate text-xs text-gray-500">${esc(source.baseUrl)}</div>
                        <div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-400">
                            <span class="rounded-full border ${source.online ? 'border-green-500/30 bg-green-500/10 text-green-300' : 'border-gray-800 bg-dark-800 text-gray-400'} px-2 py-0.5">${source.online ? 'Online' : 'Offline'}</span>
                            <span>${esc(source.songCount || 0)} songs</span>
                        </div>
                        ${offline ? `<div class='mt-2 text-xs text-red-300'>This source appears to be offline.${source.message ? ' ' + esc(source.message) : ''}</div>` : (source.message ? `<div class="mt-1 text-xs text-amber-300">${esc(source.message)}</div>` : '')}
                    </div>
                    <div class="flex flex-shrink-0 gap-2">
                        <button class="rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-300 transition hover:bg-dark-500 hover:text-white ${busy ? 'opacity-60 cursor-not-allowed' : ''}" data-rlc-refresh-source="${esc(source.providerId)}" ${busy ? 'disabled' : ''}>${busyMode === 'refresh' ? 'Refreshing...' : 'Refresh'}</button>
                        <button class="rounded-lg bg-dark-600 px-3 py-2 text-sm text-gray-300 transition hover:bg-red-900/50 hover:text-red-300 ${busy ? 'opacity-60 cursor-not-allowed' : ''}" data-rlc-remove="${esc(source.providerId)}" ${busy ? 'disabled' : ''}>${busyMode === 'remove' ? 'Removing...' : 'Remove'}</button>
                    </div>
                </div>
            </div>
            `;
        }).join('');
    }

    async function refresh() {
        setBusyState({ loading: !state.sources.length, refreshing: true });
        try {
            const status = await api('/status');
            state.sources = status.sources || [];
            renderSources();
            await refreshCoreLibraryProviders({ reloadOnChange: false });
        } finally {
            setBusyState({ loading: false, refreshing: false });
        }
    }

    async function addSource() {
        const baseUrl = normalizeBaseUrl(document.getElementById('rlc-base-url')?.value || '');
        const label = document.getElementById('rlc-label')?.value.trim() || '';
        if (!baseUrl) throw new Error('Enter a server URL (for example: http://frodo.local:8765).');
        if (state.adding) return;
        setBusyState({ adding: true });
        setMessage('Adding source...', 'neutral');
        try {
            const result = await api('/sources', { method: 'POST', body: JSON.stringify({ baseUrl, label }) });
            const added = result?.source || { baseUrl, label: label || baseUrl, online: false, songCount: 0 };
            const existingIndex = state.sources.findIndex(item => (item.providerId || '') === (added.providerId || ''));
            const viewItem = {
                ...added,
                online: false,
                message: 'Checking source status...'
            };
            if (existingIndex >= 0) state.sources[existingIndex] = { ...state.sources[existingIndex], ...viewItem };
            else state.sources.unshift(viewItem);
            renderSources();
            setMessage('Source added. Checking status...', 'success');
            clearAddForm();
            setAddFormOpen(false);
            refreshCoreLibraryProviders({ reloadOnChange: false }).catch(() => {});
            refresh().catch(error => setMessage(error.message || 'Refresh failed.', 'error'));
        } finally {
            setBusyState({ adding: false });
        }
    }

    async function refreshSource(providerId) {
        setSourceBusy(providerId, 'refresh');
        try {
            await api(`/sources/${encodeURIComponent(providerId)}/refresh`, { method: 'POST', body: JSON.stringify({}) });
            await refreshCoreLibraryProviders({ reloadOnChange: false });
            setMessage('Source refreshed.', 'success');
            await refresh();
        } finally {
            setSourceBusy(providerId, '');
        }
    }

    async function removeSource(providerId) {
        setSourceBusy(providerId, 'remove');
        try {
            await api(`/sources/${encodeURIComponent(providerId)}`, { method: 'DELETE' });
            await refreshCoreLibraryProviders({ reloadOnChange: true });
            setMessage('Source removed.', 'success');
            await refresh();
        } finally {
            setSourceBusy(providerId, '');
        }
    }

    function installHandlers() {
        if (state.installed) return;
        state.installed = true;
        document.addEventListener('click', async event => {
            const target = event.target.closest('[data-rlc-refresh],[data-rlc-toggle-add],[data-rlc-cancel-add],[data-rlc-refresh-source],[data-rlc-remove],[data-rlc-open-screen]');
            if (!target) return;
            if (target.disabled) return;
            try {
                if (target.matches('[data-rlc-refresh]')) await refresh();
                if (target.matches('[data-rlc-toggle-add]')) setAddFormOpen(!state.addOpen, { focus: true });
                if (target.matches('[data-rlc-cancel-add]')) setAddFormOpen(false);
                if (target.matches('[data-rlc-refresh-source]')) await refreshSource(target.getAttribute('data-rlc-refresh-source'));
                if (target.matches('[data-rlc-remove]')) await removeSource(target.getAttribute('data-rlc-remove'));
                if (target.matches('[data-rlc-open-screen]')) window.location.hash = '#remote-library-client';
            } catch (error) {
                setMessage(error.message || 'Action failed.', 'error');
            }
        });
        document.addEventListener('submit', async event => {
            if (!event.target.matches('[data-rlc-form]')) return;
            event.preventDefault();
            try {
                await addSource();
            } catch (error) {
                setMessage(error.message || 'Action failed.', 'error');
            }
        });
    }

    function init() {
        installHandlers();
        if (document.getElementById('remote-library-client-root')) {
            setAddFormOpen(state.addOpen);
            syncActionButtons();
            renderSources();
            refresh().catch(error => setMessage(error.message || 'Refresh failed.', 'error'));
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();