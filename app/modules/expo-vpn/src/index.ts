import { type EventSubscription } from 'expo-modules-core';

import ExpoVpnModule from './ExpoVpnModule';
import { VpnConfig, VpnStatus, VpnStatusResult, VpnStatusChangeEvent } from './ExpoVpn.types';

export { VpnConfig, VpnStatus, VpnStatusResult };

export function connect(config: VpnConfig): Promise<void> {
  return ExpoVpnModule.connect(config);
}

export function disconnect(): Promise<void> {
  return ExpoVpnModule.disconnect();
}

export function getStatus(): Promise<VpnStatusResult> {
  return ExpoVpnModule.getStatus() as Promise<VpnStatusResult>;
}

export function addStatusListener(
  listener: (event: VpnStatusChangeEvent) => void
): EventSubscription {
  return ExpoVpnModule.addListener('onStatusChange', listener);
}
