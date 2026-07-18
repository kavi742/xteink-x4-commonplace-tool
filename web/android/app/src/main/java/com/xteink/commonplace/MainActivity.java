package com.xteink.commonplace;

import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
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
