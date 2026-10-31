// Web Pager Service Worker for Push Notifications
const CACHE_NAME = 'paiger-v1';

self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(self.clients.claim());
});

// 1. Handle Web Push from Server (VAPID)
self.addEventListener('push', function(event) {
  var data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch(e) {
      data = { title: "🚨 طلبك جاهز للاستلام!", body: event.data.text() };
    }
  }

  var title = data.title || "🚨 طلبك جاهز للاستلام! 🎉";
  var options = {
    body: data.body || "مطعم البرجر الذهبي - وجبتك جاهزة، تفضل إلى الكاونتر الآن.",
    icon: "/static/img/brand-ready.png",
    badge: "/static/img/brand-logo.png",
    vibrate: [500, 200, 500, 200, 800],
    tag: "paiger-alarm-" + (data.order_id || Date.now()),
    renotify: true,
    requireInteraction: true,
    data: {
      url: data.url || "/",
      order_id: data.order_id
    },
    actions: [
      { action: "onway", title: "🏃‍♂️ أنا في طريقي للاستلام" }
    ]
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// 2. Handle Notification Click
self.addEventListener('notificationclick', function(event) {
  event.notification.close();

  var data = event.notification.data || {};
  var targetUrl = data.url || '/';

  // If user clicked the "I'm on the way" action button
  if (event.action === 'onway' && data.order_id) {
    fetch('/api/orders/' + data.order_id + '/onway', { method: 'POST' }).catch(function(){});
  }

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
      for (var i = 0; i < clientList.length; i++) {
        var client = clientList[i];
        if (client.url && 'focus' in client) {
          if (client.url.indexOf('/pager/') !== -1) {
            return client.focus();
          }
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});

// 3. Handle messages from client page to show local notifications
self.addEventListener('message', function(event) {
  if (event.data && event.data.type === 'SHOW_NOTIFICATION') {
    var p = event.data.payload || {};
    var title = p.title || "🚨 طلبك جاهز للاستلام!";
    var options = {
      body: p.body || "تفضل إلى الكاونتر للاستلام الآن.",
      icon: "/static/img/brand-ready.png",
      badge: "/static/img/brand-logo.png",
      vibrate: [500, 200, 500, 200, 800],
      tag: "pager-local-" + Date.now(),
      renotify: true,
      requireInteraction: true,
      data: { url: p.url || '/' }
    };
    self.registration.showNotification(title, options);
  }
});
