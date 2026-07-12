package com.xteink.commonplace;

import android.util.Log;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Minimal TCP forwarder. Listens on {@code listenPort} and relays every
 * connection to {@code upstreamHost:upstreamPort}.
 *
 * Purpose: bridge the Xteink X4 (connected to this phone's Wi-Fi hotspot) to the
 * homelab. The upstream is the homelab's Tailscale IP; because the phone's
 * Tailscale app is a system-wide VPN, the OS routes the upstream connection
 * through the tailnet automatically — this class needs no Tailscale integration,
 * it just opens a plain socket.
 *
 * Runs only while the app is in the foreground (started/stopped from the
 * Activity lifecycle), so no foreground service or notification is required.
 */
public class TcpRelay {
    private static final String TAG = "XteinkRelay";

    private final String upstreamHost;
    private final int upstreamPort;
    private final int listenPort;

    private volatile ServerSocket serverSocket;
    private Thread acceptThread;
    private ExecutorService pool;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicInteger connections = new AtomicInteger(0);

    public TcpRelay(String upstreamHost, int upstreamPort, int listenPort) {
        this.upstreamHost = upstreamHost;
        this.upstreamPort = upstreamPort;
        this.listenPort = listenPort;
    }

    public boolean isRunning() { return running.get(); }
    public int getConnections() { return connections.get(); }
    public String getUpstreamHost() { return upstreamHost; }
    public int getUpstreamPort() { return upstreamPort; }
    public int getListenPort() { return listenPort; }

    public synchronized void start() {
        if (running.get()) return;
        running.set(true);
        pool = Executors.newCachedThreadPool();
        acceptThread = new Thread(this::acceptLoop, "xteink-relay-accept");
        acceptThread.setDaemon(true);
        acceptThread.start();
        Log.i(TAG, "relay started :" + listenPort + " -> " + upstreamHost + ":" + upstreamPort);
    }

    public synchronized void stop() {
        if (!running.get()) return;
        running.set(false);
        try { if (serverSocket != null) serverSocket.close(); } catch (Exception ignored) {}
        if (pool != null) pool.shutdownNow();
        serverSocket = null;
        Log.i(TAG, "relay stopped");
    }

    private void acceptLoop() {
        try {
            ServerSocket ss = new ServerSocket();
            ss.setReuseAddress(true);
            ss.bind(new InetSocketAddress("0.0.0.0", listenPort));
            serverSocket = ss;
            while (running.get()) {
                final Socket client;
                try {
                    client = ss.accept();
                } catch (Exception e) {
                    if (running.get()) Log.w(TAG, "accept failed: " + e);
                    break;
                }
                pool.execute(() -> handle(client));
            }
        } catch (Exception e) {
            Log.e(TAG, "listen on :" + listenPort + " failed: " + e);
            running.set(false);
        }
    }

    private void handle(Socket client) {
        connections.incrementAndGet();
        Socket upstream = null;
        try {
            upstream = new Socket();
            upstream.connect(new InetSocketAddress(upstreamHost, upstreamPort), 8000);
            final Socket up = upstream;
            Thread t1 = new Thread(() -> pump(client, up), "xteink-relay-c2u");
            t1.setDaemon(true);
            t1.start();
            pump(up, client);   // block this thread on upstream -> client
            t1.join(1000);
        } catch (Exception e) {
            Log.w(TAG, "relay connection failed: " + e);
        } finally {
            closeQuietly(client);
            closeQuietly(upstream);
            connections.decrementAndGet();
        }
    }

    private void pump(Socket from, Socket to) {
        byte[] buf = new byte[8192];
        try {
            InputStream in = from.getInputStream();
            OutputStream out = to.getOutputStream();
            int n;
            while ((n = in.read(buf)) != -1) {
                out.write(buf, 0, n);
                out.flush();
            }
        } catch (Exception ignored) {
        } finally {
            try { to.shutdownOutput(); } catch (Exception ignored) {}
        }
    }

    private static void closeQuietly(Socket s) {
        if (s != null) try { s.close(); } catch (Exception ignored) {}
    }
}
