package com.xteink.commonplace;

import android.net.Uri;
import android.os.Bundle;
import android.webkit.HttpAuthHandler;
import android.webkit.WebView;

import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

public class MainActivity extends BridgeActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // NPM fronts the UI host with an HTTP Basic Auth Access List. A WebView
        // only applies credentials embedded in server.url to the MAIN frame — not
        // to subresources (/_app/* scripts, /api/* calls) — so those were 401'd
        // and the SPA never booted (black screen). Answer the Basic Auth challenge
        // for every request using the user:pass already in the configured URL;
        // the first success primes the WebView's per-origin credential cache so
        // subsequent asset + fetch requests carry Authorization automatically.
        Bridge bridge = getBridge();
        if (bridge != null) {
            final String[] creds = basicAuthFromUrl(bridge.getServerUrl());
            if (creds != null) {
                bridge.getWebView().setWebViewClient(new BridgeWebViewClient(bridge) {
                    @Override
                    public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
                        handler.proceed(creds[0], creds[1]);
                    }
                });
            }
        }
    }

    /** [user, pass] from a URL's userinfo (user:pass@host), or null if absent. */
    private static String[] basicAuthFromUrl(String url) {
        if (url == null) return null;
        String userInfo = Uri.parse(url).getUserInfo();
        if (userInfo == null || !userInfo.contains(":")) return null;
        int i = userInfo.indexOf(':');
        return new String[] { userInfo.substring(0, i), userInfo.substring(i + 1) };
    }

    // Hardware Back walks the WebView history (SvelteKit client-side routes
    // included) instead of closing the app. Capacitor's BridgeActivity does not
    // handle Back itself, so without this every Back press finishes the activity.
    // Falls back to the default (exit) only when there is no page to go back to.
    @Override
    public void onBackPressed() {
        WebView webView = getBridge().getWebView();
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
