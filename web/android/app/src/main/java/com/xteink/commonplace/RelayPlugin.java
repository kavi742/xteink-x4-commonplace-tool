package com.xteink.commonplace;

import android.content.Context;
import android.content.SharedPreferences;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.Collections;

/**
 * Capacitor bridge for the TCP relay. Lets the web UI read the relay status and
 * change the upstream (homelab) address. The relay itself is started/stopped by
 * {@link MainActivity} on the foreground lifecycle.
 */
@CapacitorPlugin(name = "Relay")
public class RelayPlugin extends Plugin {

    @PluginMethod
    public void status(PluginCall call) {
        call.resolve(buildStatus((MainActivity) getActivity()));
    }

    @PluginMethod
    public void setUpstream(PluginCall call) {
        String host = call.getString("host");
        Integer port = call.getInt("port", MainActivity.DEFAULT_PORT);
        if (host == null || host.trim().isEmpty()) {
            call.reject("host is required");
            return;
        }
        MainActivity act = (MainActivity) getActivity();
        SharedPreferences p = act.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
        p.edit()
            .putString(MainActivity.KEY_HOST, host.trim())
            .putInt(MainActivity.KEY_PORT, port)
            .apply();
        act.runOnUiThread(() -> { act.stopRelay(); act.startRelay(); });
        call.resolve(buildStatus(act));
    }

    private JSObject buildStatus(MainActivity act) {
        TcpRelay r = act.getRelay();
        SharedPreferences p = act.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
        JSObject o = new JSObject();
        o.put("running", r != null && r.isRunning());
        o.put("host", p.getString(MainActivity.KEY_HOST, MainActivity.DEFAULT_HOST));
        o.put("port", p.getInt(MainActivity.KEY_PORT, MainActivity.DEFAULT_PORT));
        o.put("listenPort", MainActivity.LISTEN_PORT);
        o.put("connections", r != null ? r.getConnections() : 0);
        o.put("hotspotIp", detectHotspotIp());
        return o;
    }

    /**
     * Best-effort: the phone's own IPv4 on its hotspot / tether interface — the
     * address the X4 should target. Falls back to any 192.168.x address. May be
     * null (then read it from Android's hotspot settings).
     */
    static String detectHotspotIp() {
        try {
            String fallback = null;
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                String name = ni.getName() == null ? "" : ni.getName().toLowerCase();
                boolean apLike = name.startsWith("ap") || name.startsWith("swlan")
                        || name.startsWith("wlan1") || name.contains("tether") || name.contains("rndis");
                for (InetAddress addr : Collections.list(ni.getInetAddresses())) {
                    if (!(addr instanceof Inet4Address) || addr.isLoopbackAddress()) continue;
                    String ip = addr.getHostAddress();
                    if (apLike) return ip;
                    if (ip.startsWith("192.168.")) fallback = ip;
                }
            }
            return fallback;
        } catch (Exception e) {
            return null;
        }
    }
}
