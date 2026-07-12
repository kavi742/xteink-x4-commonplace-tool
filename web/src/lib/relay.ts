import { registerPlugin, Capacitor } from '@capacitor/core';

export interface RelayStatus {
	running: boolean;
	host: string;
	port: number;
	listenPort: number;
	connections: number;
	hotspotIp?: string | null;
}

interface RelayPluginDef {
	status(): Promise<RelayStatus>;
	setUpstream(opts: { host: string; port: number }): Promise<RelayStatus>;
}

const Relay = registerPlugin<RelayPluginDef>('Relay');

/** True only inside the native Android app (where the relay plugin exists). */
export function isAndroidApp(): boolean {
	return Capacitor.getPlatform() === 'android';
}

export async function relayStatus(): Promise<RelayStatus | null> {
	if (!isAndroidApp()) return null;
	try {
		return await Relay.status();
	} catch {
		return null;
	}
}

export async function relaySetUpstream(host: string, port: number): Promise<RelayStatus | null> {
	if (!isAndroidApp()) return null;
	try {
		return await Relay.setUpstream({ host, port });
	} catch {
		return null;
	}
}
