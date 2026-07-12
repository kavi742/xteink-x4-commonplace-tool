package com.xteink.commonplace;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.util.Log;

import java.net.InetAddress;

import javax.jmdns.JmDNS;
import javax.jmdns.ServiceInfo;

/**
 * Advertises the homelab's mDNS name (e.g. {@code ghostbird.local}) on the
 * phone's Wi-Fi hotspot, pointing at the phone itself. This lets the X4 use ONE
 * fixed KOReader address ({@code http://ghostbird.local:8090}) on every network:
 *
 * <ul>
 *   <li>Home Wi-Fi: the real homelab (avahi) answers the name directly.</li>
 *   <li>Hotspot: this responder answers the same name with the phone's tether
 *       IP, so the X4 reaches the phone -> {@link TcpRelay} -> homelab.</li>
 * </ul>
 *
 * MUST only run while tethering — on the home LAN it would clash with the real
 * homelab advertising the same name. {@link MainActivity} gates it on the AP
 * interface being present. All jmDNS work happens off the main thread
 * ({@code JmDNS.create}/{@code close} do blocking network I/O).
 */
final class MdnsResponder {
    private static final String TAG = "XteinkMdns";

    private final Context ctx;
    private final String hostName;   // "ghostbird" -> answers "ghostbird.local"
    private final int port;

    private WifiManager.MulticastLock lock;
    private JmDNS jmdns;
    private volatile boolean running;

    MdnsResponder(Context ctx, String hostName, int port) {
        this.ctx = ctx.getApplicationContext();
        this.hostName = hostName;
        this.port = port;
    }

    boolean isRunning() { return running; }

    String getHostName() { return hostName; }

    /** Start advertising {@code hostName.local -> apIp}. No-op if already running. */
    synchronized void start(String apIp) {
        if (running || apIp == null) return;
        running = true;
        new Thread(() -> setup(apIp), "xteink-mdns").start();
    }

    private void setup(String apIp) {
        try {
            WifiManager wm = (WifiManager) ctx.getSystemService(Context.WIFI_SERVICE);
            WifiManager.MulticastLock l = null;
            if (wm != null) {
                l = wm.createMulticastLock("xteink-mdns");
                l.setReferenceCounted(true);
                l.acquire();
            }
            InetAddress addr = InetAddress.getByName(apIp);
            // Binding jmDNS to the AP address with this host name makes it answer
            // A queries for "<hostName>.local" with `addr`.
            JmDNS j = JmDNS.create(addr, hostName);
            j.registerService(ServiceInfo.create(
                    "_http._tcp.local.", "Xteink Commonplace", port, "path=/"));
            synchronized (this) {
                if (!running) {          // stopped while we were setting up
                    closeQuietly(j, l);
                    return;
                }
                jmdns = j;
                lock = l;
            }
            Log.i(TAG, "advertising " + hostName + ".local -> " + apIp + ":" + port);
        } catch (Exception e) {
            Log.w(TAG, "mDNS start failed: " + e);
            stop();
        }
    }

    /** Stop advertising. Safe to call repeatedly; runs teardown off the main thread. */
    synchronized void stop() {
        running = false;
        final JmDNS j = jmdns;
        final WifiManager.MulticastLock l = lock;
        jmdns = null;
        lock = null;
        if (j == null && l == null) return;
        new Thread(() -> closeQuietly(j, l), "xteink-mdns-stop").start();
    }

    private static void closeQuietly(JmDNS j, WifiManager.MulticastLock l) {
        if (j != null) {
            try { j.unregisterAllServices(); j.close(); } catch (Exception ignored) {}
        }
        if (l != null && l.isHeld()) {
            try { l.release(); } catch (Exception ignored) {}
        }
    }
}
