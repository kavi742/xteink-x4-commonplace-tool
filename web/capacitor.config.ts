import type { CapacitorConfig } from '@capacitor/cli';

// The APK loads the live web UI from the homelab over the public DuckDNS domain,
// so it works off the home network without Tailscale. NPM fronts this host with
// HTTPS + an Access List (HTTP Basic Auth). A WebView can't answer a Basic Auth
// challenge on its own (Capacitor's WebViewClient cancels it), so the credentials
// are embedded in the URL. They are injected from APP_BASIC_AUTH at `cap sync`
// time so nothing secret is committed (the generated capacitor.config.json is
// gitignored):
//
//   APP_BASIC_AUTH='user:pass' npx cap sync android
//
// Use a DEDICATED UI host (xteink.*) — NOT the kosync host (read.*), which is
// x-auth only and would 401 the X4 under a Basic Auth Access List.
const auth = process.env.APP_BASIC_AUTH ? `${process.env.APP_BASIC_AUTH}@` : '';

const config: CapacitorConfig = {
  appId: 'com.xteink.commonplace',
  appName: 'Xteink Commonplace',
  webDir: 'build',
  server: {
    // Site ROOT over HTTPS (no `/app`: the UI is served at `/`, and `/app` only
    // 307-redirects there — scoping the WebView to `/app` breaks in-app
    // navigation to /log, /tbr, …).
    url: `https://${auth}xteink.ghostbird.duckdns.org`,
  },
};

export default config;
