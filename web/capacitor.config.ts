import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.xteink.commonplace',
  appName: 'Xteink Commonplace',
  webDir: 'build',
  server: {
    // Load directly from the homelab — all /api calls are same-origin.
    // Requires the Android device to be on LAN or Tailscale.
    // Remove this block to use the bundled static build instead.
    url: 'http://192.168.86.153:8090/app',
    cleartext: true,   // allow plain HTTP (no HTTPS needed on local network)
  }
};

export default config;
