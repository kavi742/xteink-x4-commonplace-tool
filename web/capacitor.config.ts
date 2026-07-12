import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.xteink.commonplace',
  appName: 'Xteink Commonplace',
  webDir: 'build',
  server: {
    // The APK loads the live web UI from the homelab. The phone resolves
    // `ghostbird` via Tailscale MagicDNS. Swap for a real domain name later.
    url: 'http://ghostbird:8090/app',
    cleartext: true,
  },
};

export default config;
