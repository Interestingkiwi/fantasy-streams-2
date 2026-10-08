/*
 * Account sync: mirrors the localStorage keys that describe a league into the
 * signed-in account, and back onto any device the account signs in on. Also
 * drives the account modal (partials/account.html).
 *
 * Temporary, like routes/account_routes.py - delete both once Yahoo sync lands.
 *
 * **The pages are untouched.** They keep reading and writing localStorage as
 * they always have. The server renders the open league's state into the page
 * (window.FS_ACCOUNT) and this script, running before any page script, writes
 * it over this browser's copy. After that, `Storage.prototype.setItem` and
 * `removeItem` are wrapped so any write to a league key is noticed, and the
 * league is saved back a moment later. One file to delete, rather than a sync
 * call threaded through three pages.
 *
 * **Unsaved edits win over the server's copy.** A write marks the league
 * pending until the save carrying it lands. A page opened while it is still
 * pending - a save lost to a closed tab or a dropped connection - sends this
 * browser's copy up instead of overwriting it.
 *
 * **But never over a newer one.** Every save carries the `updatedAt` its copy
 * came from, and the server refuses one built on an older version (another
 * device saved since). The page then reloads onto the newer copy and says so.
 * A tab coming back into view checks for a newer copy too, and reloads onto it
 * when it has nothing unsaved - so a desktop tab left open overnight does not
 * wait for your next edit to find out about the lineups set on your phone.
 *
 * **Whose copy is this?** `fs_accountOwner` records which account and league
 * the league keys were loaded from. Keys with no owner were entered signed out
 * and are offered to the account on sign-in; keys owned by another account are
 * never carried into this one.
 *
 * **One Yahoo league is one league in an account.** Signing in where the
 * browser already holds a league the account has opens the account's copy
 * (or, if asked, replaces it with this browser's) rather than saving a second
 * - which is how each device used to end up on its own copy, and why a scrape
 * on one never reached the other. See account_routes.active_league.
 *
 * Author - Jason Druckenmiller
 * Created - 9/29/2026
 * Updated - 10/7/2026
 */
(function () {
    'use strict';

    // Must match LEAGUE_KEYS in routes/account_routes.py - a test fails if they drift.
    const LEAGUE_KEYS = [
        'fs_leagueTeams', 'fs_yahooLeagueId', 'fs_numTeams',
        'fs_selectedStats', 'fs_statWeights', 'fs_leagueMode', 'fs_pimPolarity',
        'fs_rosterMode', 'fs_rosterSlots', 'fs_playoffWeeks', 'fs_lineupSlots',
        'fs_fantasyWeeks', 'fs_standaloneMoves', 'fs_standaloneBanked',
        'fs_standaloneGoalieStats', 'fs_goalieMinimum', 'fs_lineupEdits',
        'fs_leagueTransactions', 'fs_standaloneRoster', 'fs_standaloneOpponent',
    ];
    const KEY_SET = new Set(LEAGUE_KEYS);
    const OWNER = 'fs_accountOwner';
    const PENDING = 'fs_accountPending';
    const SAVE_DELAY_MS = 1500;
    // A keepalive request's body is capped at 64 KB, and one over it is refused
    // outright - a season of transactions takes a league past that
    const KEEPALIVE_BYTES = 60 * 1024;

    const boot = window.FS_ACCOUNT || { signedIn: false };
    const $ = id => document.getElementById(id);
    const proto = Storage.prototype;
    const rawGet = proto.getItem;
    const rawSet = proto.setItem;
    const rawRemove = proto.removeItem;

    let storage = null;
    try { storage = window.localStorage; } catch (error) { storage = null; }

    function get(key) {
        try { return storage ? rawGet.call(storage, key) : null; } catch (error) { return null; }
    }
    function set(key, value) {
        try { if (storage) rawSet.call(storage, key, value); } catch (error) { /* quota: the save still carries it */ }
    }
    function remove(key) {
        try { if (storage) rawRemove.call(storage, key); } catch (error) { /* nothing to do */ }
    }
    function readJson(key) {
        try { return JSON.parse(get(key)); } catch (error) { return null; }
    }

    function collect() {
        const state = {};
        LEAGUE_KEYS.forEach(key => {
            const value = get(key);
            if (value !== null) state[key] = value;
        });
        return state;
    }

    function hydrate(state) {
        LEAGUE_KEYS.forEach(key => {
            const value = Object.prototype.hasOwnProperty.call(state, key) ? state[key] : null;
            if (value === null) remove(key);
            else if (get(key) !== value) set(key, value);
        });
    }

    function clearLeague() {
        LEAGUE_KEYS.forEach(remove);
        remove(OWNER);
        remove(PENDING);
    }

    function parse(raw) {
        try { return JSON.parse(raw); } catch (error) { return raw; }
    }

    // Worth offering to an account: a roster, or scoring chosen on draft prep
    function hasLeague(state) {
        const league = parse(state.fs_leagueTeams);
        const players = (league?.teams || []).some(team => (team.players || []).length);
        const stats = parse(state.fs_selectedStats);
        return players || (Array.isArray(stats) && stats.length > 0);
    }

    // The Yahoo League ID a league names, as account_routes.yahoo_league reads
    // it: kept as typed, so a bare number or a pasted league URL
    function yahooLeague(raw) {
        const text = String(parse(raw ?? '') ?? '').trim();
        if (/^\d{1,10}$/.test(text)) return text;
        const found = text.match(/\/hockey\/(?:\d{4}\/hockey\/)?(\d{1,10})(?:\/|$)/);
        return found ? found[1] : null;
    }

    // A name for the league list, from what the league says about itself
    function leagueName(state) {
        const league = parse(state.fs_leagueTeams);
        const mine = (league?.teams || []).find(team => String(team.id) === String(league?.mine));
        const yahoo = parse(state.fs_yahooLeagueId);
        const team = mine?.name ? String(mine.name) : '';
        const id = yahoo ? `League ${yahoo}` : '';
        return team && id ? `${team} · ${id}` : team || id;
    }

    async function send(method, url, body, options = {}) {
        const payload = body === undefined ? undefined : JSON.stringify(body);
        // Too big for keepalive: send it plainly. A page only being hidden (a
        // phone switching apps) is still alive and finishes it.
        const small = !payload || new Blob([payload]).size < KEEPALIVE_BYTES;
        const response = await fetch(url, {
            method,
            credentials: 'same-origin',
            keepalive: !!options.keepalive && small,
            headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
            body: payload,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'success') {
            const error = new Error(data.message || `The server answered ${response.status}.`);
            error.conflict = !!data.conflict;
            throw error;
        }
        return data;
    }

    // ---------------------------------------------------------------- saving

    const signedIn = !!(boot.signedIn && boot.league && storage);
    const here = { account: boot.accountId, league: boot.league };
    // The server version this browser's copy is built on
    let base = boot.updatedAt || null;
    let version = 0;
    let timer = null;
    let saving = null;
    let again = false;
    let lastError = '';
    let reloading = false;

    function isPendingHere(pending) {
        return !!(pending && pending.account === here.account && pending.league === here.league);
    }

    // Another device saved first: its copy wins, and this page reloads onto it
    function superseded(message) {
        if (reloading) return;
        reloading = true;
        clearTimeout(timer);
        timer = null;
        remove(PENDING);
        window.alert(message);
        location.reload();
    }

    function saveLeague(options = {}) {
        clearTimeout(timer);
        timer = null;
        if (saving) {
            again = true;
            return saving;
        }
        const sent = version;
        const state = collect();
        showStatus('saving');
        saving = send('PUT', `/account/api/leagues/${boot.league}`,
                      { state, name: leagueName(state), base }, { keepalive: options.keepalive })
            .then(data => {
                base = data.updatedAt || base;
                // Only a save carrying the latest write clears the flag
                if (version === sent) remove(PENDING);
                lastError = '';
                showStatus('saved');
                return true;
            })
            .catch(error => {
                if (error.conflict) {
                    superseded(options.conflict
                        || 'This league was changed on another device since this page loaded, '
                           + 'so your latest change here was not saved.\n\nReloading with the newer version.');
                    return false;
                }
                lastError = error.message;
                showStatus('error');
                return false;
            })
            .finally(() => {
                saving = null;
                if (again) {
                    again = false;
                    saveLeague();
                }
            });
        return saving;
    }

    function changed() {
        version += 1;
        set(PENDING, JSON.stringify({ ...here, base }));
        showStatus('pending');
        clearTimeout(timer);
        timer = setTimeout(saveLeague, SAVE_DELAY_MS);
    }

    // Everything still unsaved, now. True when nothing is left unsaved.
    async function flush() {
        if (!signedIn) return true;
        if (saving) await saving;
        if (!timer && !isPendingHere(readJson(PENDING))) return true;
        return saveLeague();
    }

    // Back in view: reload onto a newer copy, unless there is something unsaved here
    async function checkFresh() {
        if (reloading || timer || saving || document.visibilityState !== 'visible') return;
        if (isPendingHere(readJson(PENDING)) || !base) return;
        try {
            const data = await send('GET', `/account/api/leagues/${boot.league}/version`);
            if (data.updatedAt && data.updatedAt !== base && !timer && !saving) {
                reloading = true;
                location.reload();
            }
        } catch (error) { /* offline: the next save will find out */ }
    }

    if (signedIn) {
        const pending = readJson(PENDING);
        if (isPendingHere(pending)) {
            // This browser holds edits the server never got: send them, keep
            // them - unless another device has saved since they were made
            base = pending.base || base;
            set(OWNER, JSON.stringify(here));
            saveLeague({
                conflict: 'Changes made in this browser never reached your account, and this league '
                    + 'has since been saved from another device.\n\nKeeping the other device’s version.',
            });
        } else {
            if (pending && pending.account === here.account && pending.league) {
                // Unsaved edits to another of this account's leagues: send them
                // there before this league replaces them, if nothing newer is
                const state = collect();
                send('PUT', `/account/api/leagues/${pending.league}`,
                     { state, name: leagueName(state), base: pending.base || null },
                     { keepalive: true }).catch(() => {});
            }
            hydrate(boot.state || {});
            remove(PENDING);
            set(OWNER, JSON.stringify(here));
        }

        proto.setItem = function (key, value) {
            rawSet.call(this, key, value);
            if (this === storage && KEY_SET.has(key)) changed();
        };
        proto.removeItem = function (key) {
            rawRemove.call(this, key);
            if (this === storage && KEY_SET.has(key)) changed();
        };

        // A tab closing mid-delay: try to get the save out. keepalive caps the
        // body at 64 KB, and if it is refused the pending flag catches it next time.
        const leaving = () => { if (timer) saveLeague({ keepalive: true }); };
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') leaving();
            else checkFresh();
        });
        window.addEventListener('pagehide', leaving);
        // Restored from the back/forward cache: the page is as old as when it was left
        window.addEventListener('pageshow', event => { if (event.persisted) checkFresh(); });
        // A desktop window brought back to the front without its tab ever being
        // hidden - beside the phone that just scraped - checks too
        window.addEventListener('focus', checkFresh);
    }

    // ------------------------------------------------------------------ modal

    const modal = $('account-modal');
    const button = $('account-button');
    if (!modal || !button) return;

    const template = $('account-league-template');
    template.remove();
    template.removeAttribute('id');

    function showStatus(stateName) {
        const dot = $('account-dot');
        const line = $('account-save-status');
        if (dot) dot.classList.toggle('hidden', stateName !== 'error');
        if (!line) return;
        line.textContent = {
            saving: 'Saving…',
            pending: 'Saving…',
            saved: 'All changes saved to your account.',
            error: `Not saved (${lastError.replace(/\.$/, '')}). It will try again with your next change, `
                + 'or when you next open a page.',
        }[stateName] || 'Changes you make are saved to your account.';
        line.className = stateName === 'error' ? 'text-sm text-red-400' : 'text-sm text-gray-400';
    }

    function showError(id, message) {
        const line = $(id);
        line.textContent = message || '';
        line.classList.toggle('hidden', !message);
    }

    function when(iso) {
        if (!iso) return '';
        const date = new Date(iso);
        return Number.isNaN(date.getTime()) ? ''
            : `Saved ${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
              + ` ${date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
    }

    function renderLeagues() {
        const list = $('account-leagues');
        list.replaceChildren();
        // Copies of one Yahoo league saved before 10/5/2026, one per device:
        // every device now opens the newest (the list is newest first), and the
        // rest are kept, and said to be older copies, until deleted
        const newest = new Map();
        (boot.leagues || []).forEach(league => {
            if (league.yahoo && !newest.has(league.yahoo)) newest.set(league.yahoo, league.id);
        });
        const older = league => Boolean(league.yahoo && newest.get(league.yahoo) !== league.id);
        $('account-copies').classList.toggle('hidden', !(boot.leagues || []).some(older));
        (boot.leagues || []).forEach(league => {
            const row = template.cloneNode(true);
            const open = league.id === boot.league;
            row.classList.remove('hidden');
            row.dataset.league = league.id;
            row.querySelector('.account-league-name').textContent = league.name || 'Unnamed league';
            row.querySelector('.account-league-meta').textContent = [
                older(league) ? `Older copy of League ${league.yahoo}` : '', when(league.updatedAt),
            ].filter(Boolean).join(' · ');
            row.querySelector('.account-league-current').classList.toggle('hidden', !open);
            row.querySelector('.account-league-open').classList.toggle('hidden', open);
            list.appendChild(row);
        });
        $('account-new-league').disabled = (boot.leagues || []).length >= (boot.maxLeagues || 20);
    }

    function render() {
        $('account-signed-out').classList.toggle('hidden', signedIn);
        $('account-signed-in').classList.toggle('hidden', !signedIn);
        $('account-footer').classList.toggle('hidden', !signedIn);
        $('account-subtitle').textContent = signedIn ? `Signed in as ${boot.username}`
            : boot.unavailable ? 'Accounts are unavailable right now - your leagues stay in this browser.'
            : 'Not signed in';
        if (signedIn) {
            renderLeagues();
            showStatus(lastError ? 'error' : (timer || saving ? 'pending' : ''));
        }
    }

    function open() {
        render();
        modal.classList.remove('hidden');
        const first = signedIn ? null : $('account-username');
        if (first) first.focus();
    }

    function close() {
        modal.classList.add('hidden');
        button.focus();
    }

    function busy(on) {
        modal.querySelectorAll('button, input').forEach(el => { el.disabled = on; });
    }

    button.addEventListener('click', open);
    modal.addEventListener('click', event => {
        if (event.target === modal || event.target.closest('.account-close')) close();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !modal.classList.contains('hidden')) close();
    });

    // Sign in or create an account, then decide what happens to this browser's league
    $('account-signed-out').addEventListener('submit', async event => {
        event.preventDefault();
        const kind = event.submitter?.value === 'signup' ? 'signup' : 'signin';
        const username = $('account-username').value.trim();
        const password = $('account-password').value;
        if (!username || !password) return showError('account-error', 'Enter a username and a password.');

        const local = collect();
        const owner = readJson(OWNER);
        showError('account-error', '');
        busy(true);
        try {
            const data = await send('POST', `/account/api/${kind}`, { username, password });
            const yahoo = yahooLeague(local.fs_yahooLeagueId);
            // Newest first, so the copy every other device is using
            const same = yahoo ? (data.leagues || []).find(league => league.yahoo === yahoo) : null;
            if (!owner && hasLeague(local) && same) {
                // The account already has this Yahoo league. Saving the browser's
                // copy as another league is what left each device on its own
                // copy, so one of the two is kept: the account's, by default.
                const saved = when(same.updatedAt);
                const useAccount = window.confirm(
                    `Your account already has League ${yahoo}${saved ? ` (${saved})` : ''}.\n\n`
                    + 'OK uses your account’s copy in this browser too, so every device shows the same league. '
                    + 'Cancel keeps this browser’s copy instead, replacing the one in your account.');
                // No base: replacing the account's copy is the point
                if (!useAccount) await send('PUT', `/account/api/leagues/${same.id}`, { state: local, name: leagueName(local) });
                await send('POST', `/account/api/leagues/${same.id}/open`, { pin: false });
            } else if (!owner && hasLeague(local)) {
                // Entered signed out, so it belongs to nobody yet
                const keep = !data.leagues.length || window.confirm(
                    'This browser has a league that is not in your account yet.\n\n'
                    + 'OK saves it to your account as another league. '
                    + 'Cancel leaves it out, and your account’s league replaces it in this browser.');
                if (keep) await send('POST', '/account/api/leagues', { state: local, name: leagueName(local) });
            } else if (owner && owner.account !== data.accountId) {
                // Another account's copy, left by a session that expired: never carried over
                clearLeague();
            }
            location.reload();
        } catch (error) {
            busy(false);
            showError('account-error', error.message);
        }
    });

    // Move reminders are the account's: a device signing out stops getting them
    async function stopReminders() {
        try {
            remove('fs_remindersOn');
            const registration = await navigator.serviceWorker?.getRegistration('/');
            const subscription = await registration?.pushManager?.getSubscription();
            if (!subscription) return;
            await send('POST', '/notifications/api/unsubscribe', { endpoint: subscription.endpoint });
            await subscription.unsubscribe();
        } catch (error) { /* nothing on, or the server will drop it when the push fails */ }
    }

    $('account-signout').addEventListener('click', async () => {
        busy(true);
        const saved = await flush();
        if (!saved && !window.confirm('Your latest changes did not save to your account. Sign out anyway and lose them?')) {
            busy(false);
            return render();
        }
        try {
            await send('POST', '/account/api/signout');
        } catch (error) {
            busy(false);
            return window.alert(error.message);
        }
        await stopReminders();
        clearLeague();
        location.reload();
    });

    $('account-new-league').addEventListener('click', async () => {
        busy(true);
        try {
            await flush();
            await send('POST', '/account/api/leagues', { state: {}, name: '' });
            location.reload();
        } catch (error) {
            busy(false);
            window.alert(error.message);
        }
    });

    $('account-leagues').addEventListener('click', async event => {
        const row = event.target.closest('li[data-league]');
        if (!row) return;
        const id = Number(row.dataset.league);
        const name = row.querySelector('.account-league-name').textContent;
        if (event.target.closest('.account-league-open')) {
            busy(true);
            try {
                await flush();
                await send('POST', `/account/api/leagues/${id}/open`);
                location.reload();
            } catch (error) {
                busy(false);
                window.alert(error.message);
            }
        } else if (event.target.closest('.account-league-delete')) {
            if (!window.confirm(`Delete ${name} from your account? It cannot be undone.`)) return;
            busy(true);
            try {
                if (id === boot.league) {
                    // Nothing left to save it into; stop the save and the flag
                    clearTimeout(timer);
                    timer = null;
                    if (saving) await saving;
                }
                await send('DELETE', `/account/api/leagues/${id}`);
                if (id === boot.league) clearLeague();
                location.reload();
            } catch (error) {
                busy(false);
                window.alert(error.message);
            }
        }
    });

    $('account-delete').addEventListener('click', async () => {
        const password = $('account-delete-password').value;
        if (!password) return showError('account-delete-error', 'Enter your password to confirm.');
        if (!window.confirm(`Delete the account ${boot.username} and every league in it?`)) return;
        busy(true);
        try {
            clearTimeout(timer);
            timer = null;
            if (saving) await saving;
            await send('POST', '/account/api/delete', { password });
            clearLeague();
            location.reload();
        } catch (error) {
            busy(false);
            showError('account-delete-error', error.message);
        }
    });

    if (boot.unavailable) button.title = 'Accounts are unavailable right now.';
}());
