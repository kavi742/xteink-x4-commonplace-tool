import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.xteink.commonplace',
  appName: 'Xteink Commonplace',
  webDir: 'build',
  server: {
    // The APK loads the live web UI from the homelab. The phone resolves
    // `ghostbird` via Tailscale MagicDNS. Swap for a real domain name later.
    // Must be the site ROOT (no `/app`): the UI is served at `/`, and `/app`
    // only 307-redirects there. Pointing Capacitor at `/app` scopes the WebView
    // to that path, so in-app navigation to root routes (/log, /tbr, …) breaks.
    url: 'http://ghostbird:8090',
    cleartext: true,
  },
};

export default config;
