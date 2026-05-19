(function() {
    const state = window.__remoteLibraryClientPlugin || { installed: false, sources: [] };
    window.__remoteLibraryClientPlugin = state;

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

    async function api(path, options) {
        const response = await fetch(`/api/plugins/remote_library_client${path}`, {
            headers: { 'Content-Type': 'application/json' },
            ...(options || {}),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
        return data;
    }

    function renderSources() {
        const node = document.getElementById('remote-library-client-sources');
        if (!node) return;
        if (!state.sources.length) {
            node.innerHTML = '<div class="rounded-lg border border-gray-800 bg-dark-800 px-3 py-3 text-sm text-gray-400">No direct sources yet.</div>';
            return;
        }
        node.innerHTML = state.sources.map(source => `
            <div class="rounded-lg border border-gray-800 bg-dark-800 px-3 py-3">
                <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div class="min-w-0">
                        <div class="truncate text-sm font-semibold text-white">${esc(source.label || source.sourceName || source.baseUrl)}</div>
                        <div class="truncate text-xs text-gray-500">${esc(source.baseUrl)} | ${esc(source.songCount || 0)} songs | ${source.online ? 'Online' : 'Offline'}</div>
                        ${source.message ? `<div class="mt-1 text-xs text-amber-300">${esc(source.message)}</div>` : ''}
                    </div>
                    <div class="flex gap-2">
                        <button class="rounded-lg bg-dark-700 px-3 py-2 text-sm text-gray-200 hover:bg-dark-600" data-rlc-refresh-source="${esc(source.providerId)}">Refresh</button>
                        <button class="rounded-lg bg-dark-700 px-3 py-2 text-sm text-gray-200 hover:bg-dark-600" data-rlc-remove="${esc(source.providerId)}">Remove</button>
                    </div>
                </div>
            </div>
        `).join('');
    }

    async function refresh() {
        const status = await api('/status');
        state.sources = status.sources || [];
        renderSources();
    }

    async function addSource() {
        const baseUrl = document.getElementById('rlc-base-url')?.value.trim() || '';
        const label = document.getElementById('rlc-label')?.value.trim() || '';
        await api('/sources', { method: 'POST', body: JSON.stringify({ baseUrl, label }) });
        setMessage('Source added. It should now appear in the Library source selector.', 'success');
        await refresh();
    }

    async function refreshSource(providerId) {
        await api(`/sources/${encodeURIComponent(providerId)}/refresh`, { method: 'POST', body: JSON.stringify({}) });
        setMessage('Source refreshed.', 'success');
        await refresh();
    }

    async function removeSource(providerId) {
        await api(`/sources/${encodeURIComponent(providerId)}`, { method: 'DELETE' });
        setMessage('Source removed.', 'success');
        await refresh();
    }

    function installHandlers() {
        if (state.installed) return;
        state.installed = true;
        document.addEventListener('click', async event => {
            const target = event.target.closest('[data-rlc-refresh],[data-rlc-add],[data-rlc-refresh-source],[data-rlc-remove],[data-rlc-open-screen]');
            if (!target) return;
            try {
                if (target.matches('[data-rlc-refresh]')) await refresh();
                if (target.matches('[data-rlc-add]')) await addSource();
                if (target.matches('[data-rlc-refresh-source]')) await refreshSource(target.getAttribute('data-rlc-refresh-source'));
                if (target.matches('[data-rlc-remove]')) await removeSource(target.getAttribute('data-rlc-remove'));
                if (target.matches('[data-rlc-open-screen]')) window.location.hash = '#remote-library-client';
            } catch (error) {
                setMessage(error.message || 'Action failed.', 'error');
            }
        });
    }

    function init() {
        installHandlers();
        if (document.getElementById('remote-library-client-root')) {
            refresh().catch(error => setMessage(error.message || 'Refresh failed.', 'error'));
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();