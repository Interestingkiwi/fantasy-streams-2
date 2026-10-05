/*
 * The service worker for move reminders (move_reminders.py). Served at /sw.js
 * so its scope is the whole site. It does one job: show a pushed reminder, and
 * open its link when tapped - Yahoo's player search for the player to add, or
 * League Home. Nothing is cached and no request is intercepted.
 *
 * Author - Jason Druckenmiller
 * Created - 10/5/2026
 * Updated - 10/5/2026
 */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch (error) { data = { body: event.data?.text() }; }
    event.waitUntil(self.registration.showNotification(data.title || 'Fantasy Streams', {
        body: data.body || '',
        tag: data.tag || 'fs-reminder',
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/badge-96.png',
        data: { url: data.url || '/standalone/#free-agents' },
    }));
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    const url = new URL(event.notification.data?.url || '/standalone/#free-agents', self.location.origin).href;
    event.waitUntil((async () => {
        // League Home already open: bring it forward rather than open another
        const open = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        const same = open.find(client => client.url === url);
        if (same) return same.focus();
        return self.clients.openWindow(url);
    })());
});
