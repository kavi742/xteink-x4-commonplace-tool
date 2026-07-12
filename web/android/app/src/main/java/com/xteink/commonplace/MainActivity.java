package com.xteink.commonplace;

import android.content.SharedPreferences;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

/**
 * Hosts the Capacitor web UI and runs the X4 -> homelab TCP relay while the app
 * is in the foreground. The relay listens on {@link #LISTEN_PORT} (reachable by
 * hotspot clients) and forwards to the homelab over the phone's Tailscale VPN.
 */
public class MainActivity extends BridgeActivity {

    static final String PREFS = "xteink_relay";
    static final String KEY_HOST = "upstream_host";
    static final String KEY_PORT = "upstream_port";
    // Homelab Tailscale IP (stable). Override from the web UI via Relay.setUpstream().
    static final String DEFAULT_HOST = "100.114.210.37";
    static final int DEFAULT_PORT = 8090;
    static final int LISTEN_PORT = 8090;
    // mDNS name advertised on the phone's hotspot so the X4 can use one fixed
    // KOReader address (http://ghostbird.local:8090) on every network. Must
    // match the homelab's avahi hostname.
    static final String MDNS_NAME = "ghostbird";

    private TcpRelay relay;
    private MdnsResponder mdns;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(RelayPlugin.class);
        super.onCreate(savedInstanceState);
    }

    @Override
    public void onResume() {
        super.onResume();
        startRelay();
    }

    @Override
    public void onPause() {
        stopRelay();
        super.onPause();
    }

    synchronized void startRelay() {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        String host = p.getString(KEY_HOST, DEFAULT_HOST);
        int port = p.getInt(KEY_PORT, DEFAULT_PORT);
        boolean restart = relay == null || !relay.isRunning()
                || !relay.getUpstreamHost().equals(host) || relay.getUpstreamPort() != port;
        if (restart) {
            if (relay != null) relay.stop();
            relay = new TcpRelay(host, port, LISTEN_PORT);
            relay.start();
        }
        syncMdns();
    }

    /**
     * Advertise {@link #MDNS_NAME}.local on the phone's hotspot only. On the home
     * LAN the real homelab already answers that name, so advertising there would
     * clash — {@link RelayPlugin#detectApIp()} returns null off-hotspot.
     */
    private synchronized void syncMdns() {
        String apIp = RelayPlugin.detectApIp();
        if (apIp != null) {
            if (mdns == null) mdns = new MdnsResponder(this, MDNS_NAME, LISTEN_PORT);
            mdns.start(apIp);
        } else if (mdns != null) {
            mdns.stop();
        }
    }

    synchronized void stopRelay() {
        if (relay != null) relay.stop();
        if (mdns != null) mdns.stop();
    }

    synchronized TcpRelay getRelay() {
        return relay;
    }

    synchronized MdnsResponder getMdns() {
        return mdns;
    }
}
