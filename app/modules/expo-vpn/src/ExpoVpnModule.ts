import { NativeModule, requireNativeModule } from 'expo';

import { VpnStatusChangeEvent } from './ExpoVpn.types';

type ExpoVpnModuleEvents = {
  onStatusChange: (event: VpnStatusChangeEvent) => void;
};

declare class ExpoVpnModuleClass extends NativeModule<ExpoVpnModuleEvents> {
  connect(config: { host: string; port: number; username: string; password: string }): Promise<void>;
  disconnect(): Promise<void>;
  getStatus(): Promise<{ status: string; peers: any[] }>;
}

export default requireNativeModule<ExpoVpnModuleClass>('ExpoVpn');
